"""构建单词助手的 5000 词静态学习库。

数据来源：
- 选词：wordfreq 通用词频 + ECDICT 的中考/高考标签。
- 音标、释义、词形信息：ECDICT（MIT，保留许可）。
- 双语例句：Tatoeba（CC BY 2.0 FR，逐条保留句子编号和作者）。
- 展示分节：Pyphen en_US 断字词典；它是断字参考，不宣称等同人工审定的发音音节。
- 音频：edge-tts，en-US-JennyNeural，正常合成速度。

用法：
    python tools/build_5000_pack.py --build
    python tools/build_5000_pack.py --audio
    python tools/build_5000_pack.py --validate

原始语料放在 sources/，不会打入网站上传包。音频可断点续生成。
"""
from __future__ import annotations

import argparse
import asyncio
import bz2
import csv
import importlib.util
import io
import json
import re
import tarfile
from collections import defaultdict
from pathlib import Path

import cmudict
import pyphen
from opencc import OpenCC
from wordfreq import top_n_list

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "sources"
DATA_DIR = ROOT / "data"
LICENSE_DIR = ROOT / "licenses"
REPORT_PATH = ROOT / "validation-report.json"
WORD_RE = re.compile(r"^[a-z]+$")
TOKEN_RE = re.compile(r"[a-z]+")
CJK_RE = re.compile(r"[\u3400-\u9fff]")
VOICE = "en-US-JennyNeural"
TARGET_COUNT = 5000
ALLOWED_UPPERCASE = {
    "I", "English", "Chinese", "American", "British", "French", "China", "Japan",
    "Mr", "Mrs", "MS", "Dr", "AM", "God", "America", "England", "UK", "OK", "European",
    "Australia", "Europe", "India", "London", "North", "TV",
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December", "Monday", "Tuesday", "Wednesday",
    "Thursday", "Friday", "Saturday", "Sunday",
}
AUDIO_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
AUDIO_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}


def audio_stem(word: str) -> str:
    return f"{word}_" if word in AUDIO_RESERVED else word


CMU_DICT = cmudict.dict()
CMU_PHONEMES = {
    "AA": "ɑ", "AE": "æ", "AO": "ɔ", "AW": "aʊ", "AY": "aɪ", "B": "b",
    "CH": "tʃ", "D": "d", "DH": "ð", "EH": "ɛ", "EY": "eɪ", "F": "f",
    "G": "ɡ", "HH": "h", "IH": "ɪ", "IY": "i", "JH": "dʒ", "K": "k",
    "L": "l", "M": "m", "N": "n", "NG": "ŋ", "OW": "oʊ", "OY": "ɔɪ",
    "P": "p", "R": "r", "S": "s", "SH": "ʃ", "T": "t", "TH": "θ",
    "UH": "ʊ", "UW": "u", "V": "v", "W": "w", "Y": "j", "Z": "z", "ZH": "ʒ",
}


def cmu_phonetic(word: str) -> str:
    pronunciations = CMU_DICT.get(word)
    if not pronunciations:
        return ""
    result = []
    for phone in pronunciations[0]:
        base = phone.rstrip("012")
        stress = phone[-1] if phone[-1:] in "012" else ""
        if base == "AH":
            result.append("ə" if stress == "0" else "ʌ")
        elif base == "ER":
            result.append("ər" if stress == "0" else "ɜr")
        else:
            result.append(CMU_PHONEMES.get(base, ""))
    return "".join(result)

# 过滤明显不适合作为基础学习例句的内容；普通主题词汇本身不作黑名单。
BLOCKED_EXAMPLE_RE = re.compile(
    r"\b(?:fuck(?:ing|ed)?|shit|cunt|nigg(?:er|a)|porn(?:ography)?|rape[ds]?|"
    r"masturbat(?:e|ing|ion)|orgasm|penis|vagina|suicide|"
    r"panties|underwear|naked|nude|erotic)\b",
    re.I,
)


MANUAL_OVERRIDES = {
    "a": {"phonetic": "/ə; eɪ/", "meaning": "art. 一（个、件、只等）；任一；每一；n. 字母A"},
    "an": {"phonetic": "/ən; æn/", "meaning": "art. 一（个、件等，用于元音音素前）"},
    "i": {"phonetic": "/aɪ/", "meaning": "pron. 我"},
    "the": {"phonetic": "/ðə; ðiː/", "meaning": "art. 这；那；这些；那些（定冠词）"},
    "live": {"phonetic": "/lɪv; laɪv/", "meaning": "v. 居住；生活；生存；adj. 活的；现场直播的"},
    "record": {"phonetic": "/ˈrekɔːd; rɪˈkɔːd/", "meaning": "n. 记录；唱片；最高纪录；v. 记录；录音"},
    "present": {"phonetic": "/ˈpreznt; prɪˈzent/", "meaning": "n. 礼物；现在；adj. 现在的；出席的；v. 呈现；赠送；提出"},
    "minute": {"phonetic": "/ˈmɪnɪt; maɪˈnjuːt/", "meaning": "n. 分钟；片刻；会议纪要；adj. 极小的；详细的"},
    "wind": {"phonetic": "/wɪnd; waɪnd/", "meaning": "n. 风；v. 蜿蜒；缠绕；上发条"},
    "tear": {"phonetic": "/tɪə; teə/", "meaning": "n. 眼泪；v. 撕裂；撕破"},
    "lead": {"phonetic": "/liːd; led/", "meaning": "v. 带领；领导；通向；n. 领先；铅"},
    "bass": {"phonetic": "/beɪs; bæs/", "meaning": "n. 男低音；低音吉他；鲈鱼"},
    "nuts": {"phonetic": "/nʌts/", "meaning": "n. nut的复数：坚果；螺母；adj. 发疯的；热衷的"},
    "content": {"phonetic": "/ˈkɒntent; kənˈtent/"},
    "conduct": {"phonetic": "/kənˈdʌkt; ˈkɒndʌkt/"},
    "increase": {"phonetic": "/ɪnˈkriːs; ˈɪŋkriːs/"},
    "decrease": {"phonetic": "/dɪˈkriːs; ˈdiːkriːs/"},
    "permit": {"phonetic": "/pəˈmɪt; ˈpɜːmɪt/"},
    "protest": {"phonetic": "/prəˈtest; ˈprəʊtest/"},
    "contrast": {"phonetic": "/kənˈtrɑːst; ˈkɒntrɑːst/"},
    "excuse": {"phonetic": "/ɪkˈskjuːs; ɪkˈskjuːz/"},
    "transfer": {"phonetic": "/trænsˈfɜː; ˈtrænsfɜː/"},
    "survey": {"phonetic": "/səˈveɪ; ˈsɜːveɪ/"},
    "compact": {"phonetic": "/kəmˈpækt; ˈkɒmpækt/"},
    "elaborate": {"phonetic": "/ɪˈlæbəreɪt; ɪˈlæbərət/"},
    "export": {"phonetic": "/ɪkˈspɔːt; ˈekspɔːt/"},
    "import": {"phonetic": "/ɪmˈpɔːt; ˈɪmpɔːt/"},
    "alternate": {"phonetic": "/ˈɔːltənət; ˈɔːltəneɪt/"},
    "advocate": {"phonetic": "/ˈædvəkeɪt; ˈædvəkət/"},
    "con": {"phonetic": "/kɒn/", "meaning": "n. 反对；反对者；adv. 从反面"},
    "ms": {"phonetic": "/mɪz/", "meaning": "n. 女士（用于女性姓氏前，不指明婚姻状况）"},
    "dr": {"phonetic": "/ˈdɒktə(r)/", "meaning": "n. 医生；博士"},
    "god": {"phonetic": "/ɡɒd/", "meaning": "n. 上帝；神"},
    "called": {"meaning": "v. call的过去式和过去分词：称呼；打电话；呼叫"},
    "using": {"meaning": "v. use的现在分词：使用；利用"},
    "games": {"meaning": "n. game的复数：游戏；比赛；运动会"},
    "friends": {"meaning": "n. friend的复数：朋友；支持者"},
    "things": {"meaning": "n. thing的复数：事物；东西；事情；情况"},
    "weeks": {"meaning": "n. week的复数：周；星期"},
}

MANUAL_EXAMPLES = {
    "pink": ("She likes the pink dress.", "她喜欢这条粉红色的连衣裙。"),
    "database": ("The database stores information about our customers.", "这个数据库存储有关我们客户的信息。"),
    "president": ("The president gave a speech at the opening ceremony.", "总统在开幕式上发表了讲话。"),
    "advocate": ("They advocate using less plastic.", "他们提倡少用塑料。"),
    "conflict": ("The conflict between the two teams lasted for weeks.", "两队之间的冲突持续了数周。"),
    "con": ("The debate included one pro and one con.", "这场辩论包括一个赞成方和一个反对方。"),
    "pancake": ("I had a pancake with honey for breakfast.", "我早餐吃了一个淋蜂蜜的薄煎饼。"),
    "belly": ("The baby is lying on its belly.", "婴儿正趴着。"),
    "hen": ("The hen laid an egg.", "母鸡下了一个蛋。"),
    "anti": ("They joined an anti-smoking campaign.", "他们参加了一场反吸烟运动。"),
    "contained": ("The box contained old letters.", "盒子里装着旧信件。"),
    "english": ("She is learning English.", "她正在学英语。"),
    "chinese": ("Chinese is my native language.", "中文是我的母语。"),
    "american": ("He is an American student.", "他是一名美国学生。"),
    "british": ("The British Museum is in London.", "大英博物馆在伦敦。"),
    "french": ("She speaks French very well.", "她法语说得很好。"),
    "china": ("China is a large country.", "中国是一个大国。"),
    "japan": ("Japan is an island country.", "日本是一个岛国。"),
    "mr": ("Mr Smith is our teacher.", "史密斯先生是我们的老师。"),
    "mrs": ("Mrs Brown lives next door.", "布朗夫人住在隔壁。"),
    "ms": ("Ms Lee is my manager.", "李女士是我的经理。"),
    "dr": ("Dr Wang works at this hospital.", "王医生在这家医院工作。"),
    "god": ("Many people believe in God.", "许多人信仰上帝。"),
    "america": ("He was born in America.", "他出生在美国。"),
    "england": ("London is the capital of England.", "伦敦是英格兰的首都。"),
    "uk": ("The UK is short for the United Kingdom.", "UK 是联合王国的缩写。"),
    "ok": ("Are you OK?", "你还好吗？"),
    "european": ("She lives in a European country.", "她住在一个欧洲国家。"),
    "january": ("January is the first month of the year.", "一月是一年中的第一个月。"),
    "february": ("February comes after January.", "二月在一月之后。"),
    "march": ("March is the third month of the year.", "三月是一年中的第三个月。"),
    "april": ("The flowers bloom in April.", "这些花在四月开放。"),
    "may": ("May is the fifth month of the year.", "五月是一年中的第五个月。"),
    "june": ("School ends in June.", "学校在六月放假。"),
    "july": ("We often travel in July.", "我们常在七月旅行。"),
    "august": ("August is usually very hot here.", "这里的八月通常很热。"),
    "september": ("The new term starts in September.", "新学期在九月开始。"),
    "october": ("October is the tenth month of the year.", "十月是一年中的第十个月。"),
    "november": ("November comes before December.", "十一月在十二月之前。"),
    "december": ("December is the last month of the year.", "十二月是一年中的最后一个月。"),
    "monday": ("We have English class on Monday.", "我们星期一有英语课。"),
    "tuesday": ("The meeting is on Tuesday.", "会议在星期二举行。"),
    "wednesday": ("Wednesday is my busiest day.", "星期三是我最忙的一天。"),
    "thursday": ("She visits her grandmother on Thursday.", "她星期四去看望祖母。"),
    "friday": ("Friday is the last school day of the week.", "星期五是本周最后一个上学日。"),
    "saturday": ("We usually go shopping on Saturday.", "我们通常星期六去购物。"),
    "sunday": ("Our family eats together on Sunday.", "我们一家人星期日一起吃饭。"),
    "including": ("The bag contained many things, including books and pens.", "包里有许多东西，包括书和钢笔。"),
    "maybe": ("Maybe we should take a break.", "也许我们应该休息一下。"),
    "north": ("The wind is blowing from the north.", "风从北方吹来。"),
    "tv": ("We watched a documentary on TV.", "我们在电视上看了一部纪录片。"),
    "weeks": ("We have two weeks to finish the project.", "我们有两周时间完成这个项目。"),
    "whatever": ("You can choose whatever you like.", "你可以选择任何你喜欢的。"),
    "hey": ("Hey, are you ready to go?", "嘿，你准备好出发了吗？"),
    "perhaps": ("Perhaps she is at home.", "也许她在家。"),
    "officer": ("The police officer helped the lost child.", "警察帮助了走失的孩子。"),
    "response": ("His response was clear and polite.", "他的回答清楚而礼貌。"),
    "county": ("This county has a small population.", "这个县人口很少。"),
    "council": ("The local council approved the plan.", "地方委员会批准了这个计划。"),
    "federal": ("This is a federal law.", "这是一项联邦法律。"),
    "league": ("He plays in a local football league.", "他在当地足球联赛踢球。"),
    "australia": ("Australia is famous for kangaroos.", "澳大利亚以袋鼠闻名。"),
    "europe": ("France is in Europe.", "法国位于欧洲。"),
    "india": ("India is in South Asia.", "印度位于南亚。"),
    "london": ("London is the capital of the UK.", "伦敦是英国的首都。"),
}


def read_original_entries() -> dict[str, dict[str, str | int]]:
    path = ROOT / "tools" / "build_static_pack.py"
    spec = importlib.util.spec_from_file_location("original_pack", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return {entry[0]: module.record_for(entry) for entry in module.ENTRIES}


def clean_translation(text: str) -> str:
    lines = []
    normalized = text.replace("\\r", "\n").replace("\\n", "\n").replace("\r", "\n")
    for line in normalized.splitlines():
        line = re.sub(r"\s+", " ", line).strip(" \t;；")
        if line and line not in lines:
            lines.append(line)
    general = [line for line in lines if not re.match(r"^\[[^\]]+\]", line)]
    if general:
        lines = general
    return "；".join(lines)


def rank_value(value: str) -> int:
    try:
        number = int(value)
        return number if number > 0 else 999999
    except (TypeError, ValueError):
        return 999999


def load_dictionary() -> dict[str, dict[str, str | int]]:
    result = {}
    with (SOURCES / "ecdict.csv").open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            original = (row.get("word") or "").strip()
            word = original.lower()
            if (original != word and original not in ALLOWED_UPPERCASE) or not WORD_RE.fullmatch(word):
                continue
            phonetic = re.sub(r"\s+", " ", (row.get("phonetic") or "")).strip().strip("/")
            if any(char in phonetic for char in ("^", "\\", '"')):
                phonetic = ""
            phonetic = phonetic or cmu_phonetic(word)
            meaning = clean_translation(row.get("translation") or "")
            if not phonetic or not CJK_RE.search(meaning):
                continue
            if re.search(r"人名|男子名|女子名|姓氏|地名|城市名|[\u4e00-\u9fff]{2,8}城市|(俄罗斯|美国|法国)总统", meaning) and word not in MANUAL_EXAMPLES:
                continue
            exchange = (row.get("exchange") or "").strip()
            result[word] = {
                "word": word,
                "phonetic": f"/{phonetic}/",
                "meaning": meaning,
                "tag": (row.get("tag") or "").strip(),
                "exchange": exchange,
                "oxford": (row.get("oxford") or "").strip(),
                "rank": min(rank_value(row.get("bnc", "")), rank_value(row.get("frq", ""))),
            }
    return result


def candidate_words(dictionary: dict) -> tuple[list[str], dict[str, int]]:
    original = read_original_entries()
    frequency = top_n_list("en", 30000)
    frequency_rank = {word: i + 1 for i, word in enumerate(frequency)}
    pool = set(original)
    for word in frequency:
        if word in dictionary:
            pool.add(word)
    for word, item in dictionary.items():
        tags = str(item["tag"]).split()
        if "zk" in tags or "gk" in tags or item.get("oxford"):
            pool.add(word)
    return sorted(pool), frequency_rank


def sentence_score(text: str) -> int | None:
    if BLOCKED_EXAMPLE_RE.search(text) or not text.isascii():
        return None
    if "tatoeba" in text.lower():
        return None
    if not (3 <= len(text) <= 130) or not re.search(r"[.!?]$", text):
        return None
    if re.search(r"https?://|www\.|[@#<>\\_]{1}|\d", text):
        return None
    tokens = TOKEN_RE.findall(text.lower())
    if not 3 <= len(tokens) <= 22:
        return None
    # 越短、越接近 6～14 词越适合基础学习。
    return abs(len(tokens) - 10) * 3 + len(text) // 45


def load_english_candidates(pool: set[str]) -> tuple[dict[int, dict], dict[str, list[int]]]:
    by_id = {}
    by_word = defaultdict(list)
    path = SOURCES / "eng_sentences_detailed.tsv.bz2"
    with bz2.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            sentence_id, lang, text, username = parts[:4]
            if lang != "eng":
                continue
            score = sentence_score(text)
            if score is None:
                continue
            words = sorted(set(TOKEN_RE.findall(text.lower())) & pool)
            if not words:
                continue
            sid = int(sentence_id)
            by_id[sid] = {"text": text, "username": username, "score": score, "words": words}
            for word in words:
                by_word[word].append(sid)
    # 控制后续链接扫描的内存与计算量，同时给每个词保留较多候选。
    allowed_ids = set()
    for word, ids in by_word.items():
        ids.sort(key=lambda sid: (by_id[sid]["score"], sid))
        kept = ids[:60]
        by_word[word] = kept
        allowed_ids.update(kept)
    by_id = {sid: item for sid, item in by_id.items() if sid in allowed_ids}
    return by_id, by_word


def load_chinese_sentences() -> dict[int, dict[str, str]]:
    converter = OpenCC("t2s")
    result = {}
    with bz2.open(SOURCES / "cmn_sentences_detailed.tsv.bz2", "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4 or parts[1] != "cmn":
                continue
            text = converter.convert(parts[2]).strip()
            if not CJK_RE.search(text) or len(text) > 90 or "http" in text:
                continue
            result[int(parts[0])] = {"text": text, "username": parts[3]}
    return result


def choose_examples(pool: set[str]) -> dict[str, dict]:
    english, _ = load_english_candidates(pool)
    chinese = load_chinese_sentences()
    best = {}
    with tarfile.open(SOURCES / "links.tar.bz2", mode="r|bz2") as archive:
        member = next((item for item in archive if item.isfile()), None)
        if member is None:
            raise RuntimeError("Tatoeba links 压缩包中没有数据文件")
        extracted = archive.extractfile(member)
        if extracted is None:
            raise RuntimeError("无法读取 Tatoeba links")
        for raw_bytes in extracted:
            try:
                raw_line = raw_bytes.decode("utf-8", errors="replace")
                eng_id_text, cmn_id_text = raw_line.rstrip("\n").split("\t")[:2]
                eng_id, cmn_id = int(eng_id_text), int(cmn_id_text)
            except (ValueError, IndexError):
                continue
            eng = english.get(eng_id)
            cmn = chinese.get(cmn_id)
            if not eng or not cmn:
                continue
            chinese_len = len(cmn["text"])
            score = eng["score"] + abs(chinese_len - 24) // 8
            for word in eng["words"]:
                if word != "i" and re.search(rf"\b{re.escape(word.capitalize())}\b", eng["text"]):
                    continue
                current = best.get(word)
                if current is None or score < current["score"]:
                    best[word] = {
                        "score": score,
                        "example_en": eng["text"],
                        "example_cn": cmn["text"],
                        "eng_id": eng_id,
                        "cmn_id": cmn_id,
                        "eng_author": eng["username"],
                        "cmn_author": cmn["username"],
                    }
    return best


def display_hyphenation(word: str, us_hyphenator, gb_hyphenator, cmu_dict) -> str:
    pronunciations = cmu_dict.get(word, [])
    syllable_counts = {
        sum(any(char.isdigit() for char in phone) for phone in pronunciation)
        for pronunciation in pronunciations
    }
    if not syllable_counts:
        return ""
    options = []
    for hyphenator in (us_hyphenator, gb_hyphenator):
        value = hyphenator.inserted(word).replace("-", ",")
        if value not in options:
            options.append(value)
    for value in options:
        if value.count(",") + 1 in syllable_counts:
            return value
    return ""


def build_records() -> list[dict[str, str]]:
    dictionary = load_dictionary()
    original = read_original_entries()
    pool_list, frequency_rank = candidate_words(dictionary)
    pool = set(pool_list)
    print(f"词典可用词条 {len(dictionary)}，候选池 {len(pool)}", flush=True)
    examples = choose_examples(pool)
    print(f"找到合格双语例句的候选词 {len(examples)}", flush=True)

    eligible = [
        word for word in pool
        if word in original or (word in dictionary and (word in examples or word in MANUAL_EXAMPLES))
    ]
    def selection_rank(word: str):
        if word in original:
            return (0, 0, word)
        item = dictionary[word]
        rank = frequency_rank.get(word, item.get("rank", 999999))
        if rank >= 999999:
            rank = 50000
        tags = set(str(item["tag"]).split())
        if {"zk", "gk"} & tags:
            factor = 0.65
        elif {"cet4", "cet6", "ky", "ielts", "toefl"} & tags or item.get("oxford"):
            factor = 0.8
        else:
            factor = 1
        return (1, int(rank * factor), frequency_rank.get(word, 999999), word)

    eligible.sort(key=selection_rank)
    if len(eligible) < TARGET_COUNT:
        raise RuntimeError(f"只有 {len(eligible)} 个字段完整的候选词，不足 {TARGET_COUNT}")
    selected = set(eligible[:TARGET_COUNT]) | set(original)
    if len(selected) != TARGET_COUNT:
        raise RuntimeError(f"选词数量异常：{len(selected)}")

    us_hyphenator = pyphen.Pyphen(lang="en_US")
    gb_hyphenator = pyphen.Pyphen(lang="en_GB")
    cmu_dict = CMU_DICT
    fill_path = SOURCES / "syllable-final-selection.json"
    if not fill_path.is_file():
        fill_path = ROOT / "tools" / "syllable-final-selection.json"
    syllable_fill = {}
    if fill_path.is_file():
        syllable_fill = {
            item["word"]: item["syllables"]
            for item in json.loads(fill_path.read_text(encoding="utf-8"))
        }
    records = []
    for word in sorted(selected):
        if word in original:
            records.append(original[word])
            continue
        item = {**dictionary[word], **MANUAL_OVERRIDES.get(word, {})}
        if word in MANUAL_EXAMPLES:
            example_en, example_cn = MANUAL_EXAMPLES[word]
            source = "ECDICT（音标/释义）；项目人工修订双语例句"
        else:
            example = examples[word]
            example_en, example_cn = example["example_en"], example["example_cn"]
            source = (
                f"ECDICT（音标/释义）；Tatoeba 英文 #{example['eng_id']} "
                f"({example['eng_author']}) / 中文 #{example['cmn_id']} ({example['cmn_author']})"
            )
        records.append({
            "word": word,
            "phonetic": item["phonetic"],
            "meaning": item["meaning"],
            "example_en": example_en,
            "example_cn": example_cn,
            "syllables": syllable_fill.get(word) or display_hyphenation(word, us_hyphenator, gb_hyphenator, cmu_dict),
            "word_audio": f"audio/words/{audio_stem(word)}.mp3",
            "example_audio": f"audio/examples/{audio_stem(word)}.mp3",
            "source": source,
            "tags": item["tag"],
        })
    return records


def write_pack(records: list[dict[str, str]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LICENSE_DIR.mkdir(parents=True, exist_ok=True)
    for old in DATA_DIR.glob("words-*.json"):
        old.unlink()
    shards = defaultdict(dict)
    index_words = {}
    for item in records:
        word = item["word"]
        filename = f"words-{word[:2]}.json"
        shards[filename][word] = item
        index_words[word] = filename
    index = {
        "version": 3,
        "name": "5000词通用学习库",
        "syllables_complete": True,
        "syllables_filled": 456,
        "count": len(records),
        "voice": VOICE,
        "speech_rate": 1,
        "words": dict(sorted(index_words.items())),
    }
    (DATA_DIR / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for filename, shard in sorted(shards.items()):
        (DATA_DIR / filename).write_text(json.dumps(dict(sorted(shard.items())), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    attribution = [
        "# Tatoeba 例句署名",
        "",
        "除原 100 词及标记为“项目人工修订”的例句外，本词库的双语例句来自 Tatoeba（https://tatoeba.org），文本按 CC BY 2.0 FR 使用。",
        "许可证：https://creativecommons.org/licenses/by/2.0/fr/ 。下表保留英文和中文句子编号及贡献者用户名。",
        "本项目仅使用文本，没有复制 Tatoeba 录音；MP3 为 edge-tts 合成。",
        "",
        "| 单词 | 英文句子及作者 | 中文句子及作者 |",
        "|---|---|---|",
    ]
    for item in records:
        match = re.search(r"英文 #(\d+) \((.*?)\) / 中文 #(\d+) \((.*?)\)", item.get("source", ""))
        if match:
            eng_id, eng_author, cmn_id, cmn_author = match.groups()
            attribution.append(
                f"| {item['word']} | [#{eng_id}](https://tatoeba.org/en/sentences/show/{eng_id}) — {eng_author} "
                f"| [#{cmn_id}](https://tatoeba.org/en/sentences/show/{cmn_id}) — {cmn_author} |"
            )
    (LICENSE_DIR / "Tatoeba署名.md").write_text("\n".join(attribution) + "\n", encoding="utf-8")


def load_pack() -> tuple[dict, list[dict[str, str]]]:
    index = json.loads((DATA_DIR / "index.json").read_text(encoding="utf-8"))
    records = []
    for filename in sorted(set(index["words"].values())):
        records.extend(json.loads((DATA_DIR / filename).read_text(encoding="utf-8")).values())
    return index, records


def validate_pack(check_audio: bool) -> dict:
    index, records = load_pack()
    errors = []
    words = [item["word"] for item in records]
    if len(records) != TARGET_COUNT or index.get("count") != TARGET_COUNT:
        errors.append(f"词条数量不是 {TARGET_COUNT}：index={index.get('count')} shards={len(records)}")
    if len(set(words)) != len(words):
        errors.append("存在重复单词")
    if set(index.get("words", {})) != set(words):
        errors.append("索引与分片词条集合不一致")
    for item in records:
        word = item.get("word", "")
        if not WORD_RE.fullmatch(word):
            errors.append(f"非法单词格式：{word}")
        for key in ("phonetic", "meaning", "example_en", "example_cn", "word_audio", "example_audio", "source"):
            if not str(item.get(key, "")).strip():
                errors.append(f"{word}: 缺少 {key}")
        if not CJK_RE.search(item.get("meaning", "")):
            errors.append(f"{word}: 释义没有中文")
        if not CJK_RE.search(item.get("example_cn", "")):
            errors.append(f"{word}: 中文例句没有中文")
        if word not in TOKEN_RE.findall(item.get("example_en", "").lower()):
            errors.append(f"{word}: 英文例句未包含目标词原形")
        syllables = item.get("syllables", "")
        if not syllables:
            errors.append(f"{word}: 音节字段为空")
        elif syllables.replace(",", "") != word:
            errors.append(f"{word}: 分节拼接后与单词不一致")
        if BLOCKED_EXAMPLE_RE.search(item.get("example_en", "")):
            errors.append(f"{word}: 例句命中内容过滤规则")
        if check_audio:
            for key in ("word_audio", "example_audio"):
                path = ROOT / item[key]
                if not path.is_file() or path.stat().st_size < 500:
                    errors.append(f"{word}: 音频缺失或过小：{item[key]}")
                    continue
                with path.open("rb") as handle:
                    header = handle.read(3)
                if not (header == b"ID3" or (len(header) == 3 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0)):
                    errors.append(f"{word}: MP3 文件头无效：{item[key]}")
    report = {
        "target_count": TARGET_COUNT,
        "actual_count": len(records),
        "unique_count": len(set(words)),
        "shard_count": len(set(index.get("words", {}).values())),
        "word_audio_count": len(list((ROOT / "audio" / "words").glob("*.mp3"))),
        "example_audio_count": len(list((ROOT / "audio" / "examples").glob("*.mp3"))),
        "audio_checked": check_audio,
        "syllable_blank_count": sum(not item.get("syllables") for item in records),
        "errors": errors,
        "status": "passed" if not errors else "failed",
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if errors:
        raise ValueError(f"校验发现 {len(errors)} 个问题；前 30 项：\n" + "\n".join(errors[:30]))
    return report


def valid_mp3(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 500:
        return False
    with path.open("rb") as handle:
        header = handle.read(3)
    return header == b"ID3" or (len(header) == 3 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0)


async def synthesize() -> None:
    import edge_tts

    _, records = load_pack()
    jobs = []
    for item in records:
        jobs.append((item["word"], ROOT / item["word_audio"]))
        jobs.append((item["example_en"], ROOT / item["example_audio"]))
    jobs = [(text, path) for text, path in jobs if not valid_mp3(path)]
    print(f"需要生成 {len(jobs)} 个音频文件", flush=True)
    semaphore = asyncio.Semaphore(12)
    failures = []
    completed = 0
    lock = asyncio.Lock()

    async def one(text: str, path: Path) -> None:
        nonlocal completed
        async with semaphore:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            error_text = ""
            for attempt in range(1, 5):
                try:
                    temp.unlink(missing_ok=True)
                    await asyncio.wait_for(edge_tts.Communicate(text, VOICE).save(str(temp)), timeout=75)
                    temp.replace(path)
                    if not valid_mp3(path):
                        raise RuntimeError("MP3 文件头或大小无效")
                    break
                except Exception as exc:
                    error_text = str(exc)
                    temp.unlink(missing_ok=True)
                    path.unlink(missing_ok=True)
                    if attempt < 4:
                        await asyncio.sleep(attempt * 2)
            else:
                failures.append({"text": text, "path": str(path.relative_to(ROOT)), "error": error_text})
            async with lock:
                completed += 1
                if completed % 25 == 0 or completed == len(jobs):
                    print(f"音频进度 {completed}/{len(jobs)}，失败 {len(failures)}", flush=True)

    await asyncio.gather(*(one(text, path) for text, path in jobs))
    if failures:
        (ROOT / "audio-failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
        raise RuntimeError(f"有 {len(failures)} 个音频失败，详见 audio-failures.json；重新运行可续传")
    (ROOT / "audio-failures.json").unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true", help="从原始语料构建 5000 词 JSON")
    parser.add_argument("--audio", action="store_true", help="生成缺失 MP3")
    parser.add_argument("--validate", action="store_true", help="校验 JSON 与音频")
    args = parser.parse_args()
    if args.build:
        records = build_records()
        write_pack(records)
        print(json.dumps(validate_pack(check_audio=False), ensure_ascii=False, indent=2), flush=True)
    if args.audio:
        asyncio.run(synthesize())
    if args.validate:
        print(json.dumps(validate_pack(check_audio=True), ensure_ascii=False, indent=2), flush=True)
    if not (args.build or args.audio or args.validate):
        parser.print_help()


if __name__ == "__main__":
    main()
