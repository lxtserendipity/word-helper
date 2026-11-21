"""生成单词助手的 100 词静态样板包。

用法：
    python tools/build_static_pack.py           # 只生成并校验 JSON
    python tools/build_static_pack.py --audio   # 同时生成缺失的 MP3

音频使用 edge-tts；重复执行会跳过已经存在且非空的音频文件。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
AUDIO_DIR = ROOT / "audio"
VOICE = "en-US-JennyNeural"

# word, IPA（美式为主）, 释义, 英文例句, 中文例句, 展示用音节划分
ENTRIES = [
    ("teacher", "ˈtiːtʃər", "n. 老师；教师", "My teacher is very kind.", "我的老师很友善。", "tea,cher"),
    ("student", "ˈstjuːdənt", "n. 学生", "He is a good student.", "他是一个好学生。", "stu,dent"),
    ("book", "bʊk", "n. 书；书籍", "This book is very interesting.", "这本书很有趣。", "book"),
    ("computer", "kəmˈpjuːtər", "n. 电脑；计算机", "I use my computer every day.", "我每天都使用电脑。", "com,pu,ter"),
    ("friend", "frend", "n. 朋友", "He is my best friend.", "他是我最好的朋友。", "friend"),
    ("school", "skuːl", "n. 学校", "I walk to school every day.", "我每天步行去学校。", "school"),
    ("class", "klæs", "n. 班级；课", "Our class begins at eight.", "我们的课八点开始。", "class"),
    ("family", "ˈfæməli", "n. 家庭；家人", "I love my family.", "我爱我的家人。", "fam,i,ly"),
    ("father", "ˈfɑːðər", "n. 父亲；爸爸", "My father works in a hospital.", "我的爸爸在医院工作。", "fa,ther"),
    ("mother", "ˈmʌðər", "n. 母亲；妈妈", "My mother cooks dinner every evening.", "我的妈妈每天傍晚做晚饭。", "mo,ther"),
    ("sister", "ˈsɪstər", "n. 姐姐；妹妹", "My sister likes to sing.", "我的姐姐喜欢唱歌。", "sis,ter"),
    ("brother", "ˈbrʌðər", "n. 哥哥；弟弟", "My brother plays basketball.", "我的哥哥打篮球。", "bro,ther"),
    ("parent", "ˈperənt", "n. 父亲或母亲；家长", "Every parent cares about their child.", "每位家长都关心自己的孩子。", "par,ent"),
    ("child", "tʃaɪld", "n. 孩子；儿童", "The child is playing in the park.", "那个孩子正在公园里玩。", "child"),
    ("apple", "ˈæpəl", "n. 苹果", "She eats an apple every day.", "她每天吃一个苹果。", "ap,ple"),
    ("banana", "bəˈnænə", "n. 香蕉", "The banana is yellow.", "这根香蕉是黄色的。", "ba,na,na"),
    ("orange", "ˈɔːrɪndʒ", "n. 橙子；橙色", "I drink orange juice in the morning.", "我早上喝橙汁。", "or,ange"),
    ("water", "ˈwɔːtər", "n. 水", "Please drink some water.", "请喝一些水。", "wa,ter"),
    ("bread", "bred", "n. 面包", "I have bread and milk for breakfast.", "我早餐吃面包、喝牛奶。", "bread"),
    ("milk", "mɪlk", "n. 牛奶", "The milk is fresh.", "这牛奶很新鲜。", "milk"),
    ("rice", "raɪs", "n. 米饭；大米", "We eat rice for dinner.", "我们晚餐吃米饭。", "rice"),
    ("egg", "eɡ", "n. 鸡蛋；蛋", "He boiled an egg for breakfast.", "他早餐煮了一个鸡蛋。", "egg"),
    ("meat", "miːt", "n. 肉", "This meat is fresh.", "这肉很新鲜。", "meat"),
    ("fruit", "fruːt", "n. 水果", "Fruit is good for your health.", "水果有益健康。", "fruit"),
    ("food", "fuːd", "n. 食物", "The food smells good.", "这食物闻起来很香。", "food"),
    ("breakfast", "ˈbrekfəst", "n. 早餐", "I eat breakfast at seven.", "我七点吃早餐。", "break,fast"),
    ("lunch", "lʌntʃ", "n. 午餐", "We have lunch at school.", "我们在学校吃午餐。", "lunch"),
    ("dinner", "ˈdɪnər", "n. 晚餐；正餐", "My family has dinner together.", "我的家人一起吃晚饭。", "din,ner"),
    ("dog", "dɔːɡ", "n. 狗", "The dog is running in the park.", "那只狗正在公园里跑。", "dog"),
    ("cat", "kæt", "n. 猫", "The cat is sleeping under the table.", "那只猫正在桌子下面睡觉。", "cat"),
    ("bird", "bɜːrd", "n. 鸟", "A bird is singing in the tree.", "一只鸟正在树上唱歌。", "bird"),
    ("fish", "fɪʃ", "n. 鱼", "The fish swims in the water.", "那条鱼在水里游。", "fish"),
    ("rabbit", "ˈræbɪt", "n. 兔子", "The rabbit has long ears.", "这只兔子有长耳朵。", "rab,bit"),
    ("tiger", "ˈtaɪɡər", "n. 老虎", "The tiger is a strong animal.", "老虎是一种强壮的动物。", "ti,ger"),
    ("lion", "ˈlaɪən", "n. 狮子", "The lion lives in Africa.", "这头狮子生活在非洲。", "li,on"),
    ("panda", "ˈpændə", "n. 熊猫", "The panda eats bamboo.", "这只熊猫吃竹子。", "pan,da"),
    ("monkey", "ˈmʌŋki", "n. 猴子", "The monkey is climbing a tree.", "那只猴子正在爬树。", "mon,key"),
    ("elephant", "ˈelɪfənt", "n. 大象", "The elephant has a long trunk.", "这头大象有长鼻子。", "el,e,phant"),
    ("red", "red", "adj. 红色的；n. 红色", "The red flower is beautiful.", "这朵红花很漂亮。", "red"),
    ("blue", "bluː", "adj. 蓝色的；n. 蓝色", "The sky is blue today.", "今天的天空很蓝。", "blue"),
    ("green", "ɡriːn", "adj. 绿色的；n. 绿色", "The leaves are green in spring.", "春天叶子是绿色的。", "green"),
    ("yellow", "ˈjeloʊ", "adj. 黄色的；n. 黄色", "The yellow dress is pretty.", "这条黄色连衣裙很漂亮。", "yel,low"),
    ("black", "blæk", "adj. 黑色的；n. 黑色", "My shoes are black.", "我的鞋是黑色的。", "black"),
    ("white", "waɪt", "adj. 白色的；n. 白色", "The white cat is cute.", "这只白猫很可爱。", "white"),
    ("color", "ˈkʌlər", "n. 颜色", "What is your favorite color?", "你最喜欢什么颜色？", "col,or"),
    ("day", "deɪ", "n. 一天；白天", "Today is a sunny day.", "今天是晴朗的一天。", "day"),
    ("week", "wiːk", "n. 星期；周", "There are seven days in a week.", "一周有七天。", "week"),
    ("month", "mʌnθ", "n. 月；月份", "December is the last month of the year.", "十二月是一年中的最后一个月。", "month"),
    ("year", "jɪr", "n. 年；年份", "The new year begins in January.", "新的一年从一月开始。", "year"),
    ("today", "təˈdeɪ", "adv. 在今天；n. 今天", "I am very busy today.", "我今天很忙。", "to,day"),
    ("morning", "ˈmɔːrnɪŋ", "n. 早晨；上午", "I read English in the morning.", "我早上读英语。", "morn,ing"),
    ("afternoon", "ˌæftərˈnuːn", "n. 下午", "We play sports in the afternoon.", "我们下午做运动。", "af,ter,noon"),
    ("evening", "ˈiːvnɪŋ", "n. 傍晚；晚上", "My family watches TV in the evening.", "我的家人晚上看电视。", "eve,ning"),
    ("night", "naɪt", "n. 夜晚；夜间", "The stars are bright at night.", "夜晚星星很明亮。", "night"),
    ("spring", "sprɪŋ", "n. 春天", "Flowers bloom in spring.", "花儿在春天开放。", "spring"),
    ("summer", "ˈsʌmər", "n. 夏天", "We often swim in summer.", "我们经常在夏天游泳。", "sum,mer"),
    ("autumn", "ˈɔːtəm", "n. 秋天", "The leaves turn yellow in autumn.", "秋天树叶变黄。", "au,tumn"),
    ("winter", "ˈwɪntər", "n. 冬天", "Winter is the coldest season.", "冬天是最冷的季节。", "win,ter"),
    ("weather", "ˈweðər", "n. 天气", "The weather is nice today.", "今天天气很好。", "weath,er"),
    ("sunny", "ˈsʌni", "adj. 晴朗的", "It is a sunny day.", "这是晴朗的一天。", "sun,ny"),
    ("rainy", "ˈreɪni", "adj. 下雨的；多雨的", "We stay at home on rainy days.", "下雨天我们待在家里。", "rain,y"),
    ("windy", "ˈwɪndi", "adj. 有风的；多风的", "It is too windy to go outside.", "风太大了，不适合外出。", "wind,y"),
    ("cloudy", "ˈklaʊdi", "adj. 多云的；阴天的", "The sky is cloudy today.", "今天天空多云。", "cloud,y"),
    ("snowy", "ˈsnoʊi", "adj. 下雪的；多雪的", "The children like snowy days.", "孩子们喜欢下雪天。", "snow,y"),
    ("run", "rʌn", "v. 跑；跑步", "I run in the park every morning.", "我每天早上在公园跑步。", "run"),
    ("walk", "wɔːk", "v. 步行；散步", "We walk to school together.", "我们一起步行去学校。", "walk"),
    ("jump", "dʒʌmp", "v. 跳；跳跃", "The boy can jump very high.", "这个男孩能跳得很高。", "jump"),
    ("swim", "swɪm", "v. 游泳", "She can swim very well.", "她游泳游得很好。", "swim"),
    ("read", "riːd", "v. 读；阅读", "I read a story every night.", "我每晚读一个故事。", "read"),
    ("write", "raɪt", "v. 写；写字", "Please write your name here.", "请在这里写下你的名字。", "write"),
    ("listen", "ˈlɪsən", "v. 听；倾听", "Listen to the teacher carefully.", "认真听老师讲课。", "lis,ten"),
    ("speak", "spiːk", "v. 说；讲话", "She can speak English well.", "她英语说得很好。", "speak"),
    ("sing", "sɪŋ", "v. 唱歌", "The girls sing a song together.", "女孩们合唱一首歌。", "sing"),
    ("dance", "dæns", "v. 跳舞", "The children dance happily.", "孩子们开心地跳舞。", "dance"),
    ("draw", "drɔː", "v. 画；绘画", "I draw a picture of my family.", "我画一幅全家福。", "draw"),
    ("play", "pleɪ", "v. 玩；参加（运动）；演奏", "They play football after school.", "他们放学后踢足球。", "play"),
    ("work", "wɜːrk", "v. 工作；n. 工作", "My parents work hard every day.", "我的父母每天努力工作。", "work"),
    ("study", "ˈstʌdi", "v. 学习；n. 学习；书房", "I study English at school.", "我在学校学习英语。", "stud,y"),
    ("learn", "lɜːrn", "v. 学习；学会", "We learn new words every day.", "我们每天学习新单词。", "learn"),
    ("teach", "tiːtʃ", "v. 教；讲授", "Ms. Li will teach us English today.", "李老师今天将教我们英语。", "teach"),
    ("help", "help", "v. 帮助；n. 帮助", "Can you help me, please?", "请问你能帮助我吗？", "help"),
    ("open", "ˈoʊpən", "v. 打开；adj. 开着的", "Please open the window.", "请打开窗户。", "o,pen"),
    ("close", "kloʊz", "v. 关闭；合上", "Please close the door when you leave.", "离开时请关门。", "close"),
    ("give", "ɡɪv", "v. 给；给予", "Give me the red pen, please.", "请把那支红笔给我。", "give"),
    ("take", "teɪk", "v. 拿；带走", "Take your umbrella with you.", "带上你的雨伞。", "take"),
    ("make", "meɪk", "v. 制作；使", "I make a card for my mother.", "我为妈妈制作一张卡片。", "make"),
    ("come", "kʌm", "v. 来；来到", "Come and look at this picture.", "过来看看这幅画。", "come"),
    ("go", "ɡoʊ", "v. 去；走", "We go home at five o'clock.", "我们五点钟回家。", "go"),
    ("like", "laɪk", "v. 喜欢", "I like my new school.", "我喜欢我的新学校。", "like"),
    ("love", "lʌv", "v. 爱；热爱；n. 爱", "They love their little dog.", "他们爱他们的小狗。", "love"),
    ("want", "wɑːnt", "v. 想要", "I want a glass of water.", "我想要一杯水。", "want"),
    ("need", "niːd", "v. 需要；n. 需要", "We need some milk and eggs.", "我们需要一些牛奶和鸡蛋。", "need"),
    ("see", "siː", "v. 看见；看到", "I see a bird in the tree.", "我看见树上有一只鸟。", "see"),
    ("watch", "wɑːtʃ", "v. 观看；注视", "We watch a movie on Friday night.", "我们星期五晚上看电影。", "watch"),
    ("eat", "iːt", "v. 吃", "Eat your vegetables every day.", "每天都要吃蔬菜。", "eat"),
    ("drink", "drɪŋk", "v. 喝；n. 饮料", "Drink some water after running.", "跑步后喝一些水。", "drink"),
    ("sleep", "sliːp", "v. 睡觉；n. 睡眠", "I sleep eight hours every night.", "我每晚睡八个小时。", "sleep"),
    ("buy", "baɪ", "v. 买；购买", "I want to buy a new book.", "我想买一本新书。", "buy"),
    ("use", "juːz", "v. 使用；利用", "You can use my pencil.", "你可以使用我的铅笔。", "use"),
    ("happy", "ˈhæpi", "adj. 快乐的；幸福的", "The happy children are playing outside.", "快乐的孩子们正在外面玩。", "hap,py"),
]


def record_for(entry: tuple[str, ...]) -> dict[str, str]:
    word, phonetic, meaning, example_en, example_cn, syllables = entry
    return {
        "word": word,
        "phonetic": f"/{phonetic}/",
        "meaning": meaning,
        "example_en": example_en,
        "example_cn": example_cn,
        "syllables": syllables,
        "word_audio": f"audio/words/{word}.mp3",
        "example_audio": f"audio/examples/{word}.mp3",
        "source": "内置100词样板库",
    }


def validate(records: list[dict[str, str]], check_audio: bool) -> None:
    errors: list[str] = []
    words = [item["word"] for item in records]
    if len(records) != 100:
        errors.append(f"词条数量应为 100，实际为 {len(records)}")
    if len(set(words)) != len(words):
        errors.append("存在重复单词")
    for item in records:
        word = item["word"]
        for key in ("phonetic", "meaning", "example_en", "example_cn", "syllables"):
            if not item[key].strip():
                errors.append(f"{word}: 缺少 {key}")
        if item["syllables"].replace(",", "") != word:
            errors.append(f"{word}: 音节拼接后与单词不一致")
        if not re.search(rf"\b{re.escape(word)}\b", item["example_en"], re.I):
            errors.append(f"{word}: 例句未包含目标词原形")
        if check_audio:
            for key in ("word_audio", "example_audio"):
                path = ROOT / item[key]
                if not path.is_file() or path.stat().st_size < 500:
                    errors.append(f"{word}: 音频缺失或过小：{item[key]}")
    if errors:
        raise ValueError("\n".join(errors))


def write_json(records: list[dict[str, str]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    shards: dict[str, dict[str, dict[str, str]]] = {}
    index_words: dict[str, str] = {}
    for item in records:
        word = item["word"]
        filename = f"words-{word[:2]}.json"
        shards.setdefault(filename, {})[word] = item
        index_words[word] = filename
    index = {
        "version": 1,
        "name": "100词静态样板库",
        "count": len(records),
        "voice": VOICE,
        "words": dict(sorted(index_words.items())),
    }
    (DATA_DIR / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for filename, shard in sorted(shards.items()):
        (DATA_DIR / filename).write_text(
            json.dumps(dict(sorted(shard.items())), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


async def synthesize(records: list[dict[str, str]]) -> None:
    try:
        import edge_tts
    except ImportError as exc:
        raise SystemExit("请先执行：python -m pip install edge-tts") from exc

    jobs: list[tuple[str, Path]] = []
    for item in records:
        jobs.append((item["word"], ROOT / item["word_audio"]))
        jobs.append((item["example_en"], ROOT / item["example_audio"]))
    jobs = [(text, path) for text, path in jobs if not path.is_file() or path.stat().st_size < 500]
    print(f"需要生成 {len(jobs)} 个音频文件")
    semaphore = asyncio.Semaphore(4)

    async def one(text: str, path: Path, number: int) -> None:
        async with semaphore:
            path.parent.mkdir(parents=True, exist_ok=True)
            for attempt in range(1, 4):
                try:
                    await edge_tts.Communicate(text, VOICE).save(str(path))
                    if path.stat().st_size < 500:
                        raise RuntimeError("生成的音频过小")
                    print(f"[{number}/{len(jobs)}] {path.name} ({path.stat().st_size} bytes)")
                    return
                except Exception as exc:
                    path.unlink(missing_ok=True)
                    if attempt == 3:
                        raise
                    print(f"重试 {path.name}：{exc}")
                    await asyncio.sleep(attempt * 1.5)

    await asyncio.gather(*(one(text, path, i + 1) for i, (text, path) in enumerate(jobs)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", action="store_true", help="生成缺失的 MP3")
    args = parser.parse_args()
    records = [record_for(entry) for entry in ENTRIES]
    validate(records, check_audio=False)
    write_json(records)
    if args.audio:
        asyncio.run(synthesize(records))
    validate(records, check_audio=args.audio)
    print(f"完成：{len(records)} 个单词，{len(list(DATA_DIR.glob('words-*.json')))} 个数据分片")


if __name__ == "__main__":
    main()
