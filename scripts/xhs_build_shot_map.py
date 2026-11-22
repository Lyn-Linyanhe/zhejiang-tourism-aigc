"""Build the province-wide shot-intent map.

Combines the curated Shaoxing rows with new Hangzhou / Ningbo / light-coverage
rows into one table. Every row must cite note ids that exist in the provincial
note table; entity ids must agree with the knowledge base.

Output: docs/research/zhejiang-shot-intents.csv
"""

from __future__ import annotations

import csv
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOTES = ROOT / "docs/research/xhs-zhejiang-mcp-notes.csv"
SHAOXING = ROOT / "docs/research/shaoxing-shot-intents.csv"
OUT = ROOT / "docs/research/zhejiang-shot-intents.csv"
KB_DIR = ROOT / "data_zhejiang_kb"

CITY_ORDER = ["杭州", "宁波", "绍兴", "温州", "嘉兴", "湖州", "金华", "衢州", "舟山", "台州", "丽水"]
TAGS = {"establishing", "landmark", "detail", "water", "boat", "people", "food",
        "night", "season", "overlook", "interior", "transition", "route",
        "light", "context"}
SLUGS = {
    "西湖": "xihu", "灵隐寺·飞来峰": "lingyinsi", "西溪国家湿地公园": "xixi",
    "京杭大运河·拱宸桥": "gongchenqiao", "良渚古城遗址": "liangzhu",
    "天一阁·月湖": "tianyige", "老外滩": "laowaitan", "东钱湖": "dongqianhu",
    "兰亭": "lanting", "东湖": "donghu", "安昌古镇": "anchang", "仓桥直街": "cangqiao",
    "鲁迅故里·沈园": "luxun", "大禹陵": "dayuling", "江心屿": "jiangxinyu",
    "南湖": "nanhu", "南浔古镇": "nanxun", "横店影视城": "hengdian",
    "江郎山": "jianglangshan", "普陀山": "putuoshan", "台州府城墙": "taizhou-fucheng",
    "仙都": "xiandu",
    "雁荡山": "yandangshan", "楠溪江": "nanxijiang", "乌镇": "wuzhen", "西塘": "xitang",
    "莫干山": "moganshan", "太湖旅游度假区": "taihu", "双龙洞": "shuanglongdong",
    "诸葛八卦村": "zhugecun", "廿八都古镇": "nianbadu", "龙游石窟": "longyoushiku",
    "朱家尖": "zhujiajian", "东极岛": "dongjidao", "神仙居": "shenxianju",
    "石塘": "shitang", "云和梯田": "yunhetitian", "松阳古村落": "songyang",
}

# city, entity_id, entity_name, in_kb, spot, shot_tag, angle_type, intent,
# motif, composition, best_window, confidence, note_ids, claim_risk
NEW_ROWS = [
    # ---------------- 杭州 ----------------
    ("杭州", "hz-xihu", "西湖", True, "西湖", "season", "shot",
     "秋日红枫：花港观鱼→太子湾→杨公堤→北山街，红叶、梧桐与民国建筑一路铺开",
     "红枫/梧桐/湖面/民国建筑", "沿湖跟移，红叶做前景，北山街逆光收尾", "11月下旬-12月初，晴天下午",
     "high", ["6929967e000000001e00220a", "6ae2bc58000000001f03450e", "69f6cc46000000001a03546e"],
     "红叶期每年不同，以实时花讯为准"),
    ("杭州", "hz-xihu", "西湖", True, "西湖", "landmark", "shot",
     "经典环线：断桥→白堤→孤山→苏堤→雷峰塔，用一条动线交代西湖尺度",
     "断桥/白堤/雷峰塔", "低机位湖面接桥体；苏堤做纵深", "清晨人少",
     "high", ["6a86f445000000002202ca93", "66267185000000000103260f", "6ab87dfa000000001c02eb5c"],
     "雷峰塔/游船票价待核；断桥周末拥挤"),
    ("杭州", "hz-xihu", "西湖", True, "西湖", "water", "shot",
     "摇橹船/游船视角：三潭印月与湖心岛，从水面看城市天际线",
     "游船/三潭印月/湖面", "船头低机位；水面倒影对称", "傍晚",
     "medium", ["66267185000000000103260f", "6a86f445000000002202ca93"], "船票与班次待核"),
    ("杭州", "hz-xihu", "西湖", True, "西湖", "light", "shot",
     "西线秘境：杨公堤以西，茅家埠、乌龟潭人少水面开阔，适合“避开人流”叙事",
     "杨公堤/茅家埠/乌龟潭", "长焦压缩湖面；林间光斑", "工作日上午",
     "medium", ["69f6cc46000000001a03546e", "6ab87dfa000000001c02eb5c"], ""),
    ("杭州", "hz-xihu", "西湖", True, "西湖", "context", "narrative",
     "人流预期：断桥/湖滨周末极挤，脚本避免“空镜西湖”；清晨或西线是替代方案",
     "—", "—", "—",
     "medium", ["6a86f445000000002202ca93", "69f6cc46000000001a03546e", "6a450083000000000f0145d1"], ""),

    ("杭州", "hz-lingyinsi", "灵隐寺·飞来峰", True, "灵隐寺·飞来峰", "landmark", "shot",
     "金匾与黄墙：1号门“灵隐寺”金匾、咫尺西天黄墙，开场定调",
     "金匾/黄墙/山门", "正面平视；牌匾局部特写", "7:30前到，8:00前入场",
     "high", ["6a3dd8f90000000011014319", "6aec7430000000001a023f29"], "预约规则与开放时间待核"),
    ("杭州", "hz-lingyinsi", "灵隐寺·飞来峰", True, "灵隐寺·飞来峰", "detail", "shot",
     "飞来峰水月观音：崖壁造像、树影与冷泉溪，慢推讲“石能言”",
     "摩崖造像/树影/溪水", "浅景深特写；缓慢推近", "上午侧光",
     "high", ["6aa936ea0000000011033ec6"], "造像年代1288年待核"),
    ("杭州", "hz-lingyinsi", "灵隐寺·飞来峰", True, "灵隐寺·飞来峰", "route", "shot",
     "祈福动线：天王殿→大雄宝殿→观音殿→济公殿，按求愿主题剪",
     "殿宇/香火/匾额", "段落式仰拍；香火做转场", "上午",
     "medium", ["6a3933440000000017009f1d", "6aec7430000000001a023f29"], "祈福说法来自笔记，不作事实"),
    ("杭州", "hz-lingyinsi", "灵隐寺·飞来峰", True, "灵隐寺·飞来峰", "context", "narrative",
     "预约与人流：免费不免票，需提前1-7天预约；7号门排队短但1号门才有金匾",
     "—", "—", "—",
     "high", ["6a61a565000000002201a057", "6aec7430000000001a023f29", "6a3933440000000017009f1d"],
     "预约入口与规则以官方小程序为准"),

    ("杭州", "hz-xixi", "西溪国家湿地公园", True, "西溪国家湿地公园", "boat", "shot",
     "摇橹船穿水杉/芦苇：水面视角，船桨、涟漪与倒影",
     "摇橹船/水杉/芦苇", "船头低机位；水面倒影", "深秋至初冬",
     "high", ["6ae08e020000000034027e50", "6a3fdc60000000000802502e"], "船票与末班时间待核"),
    ("杭州", "hz-xixi", "西溪国家湿地公园", True, "西溪国家湿地公园", "landmark", "shot",
     "河渚街“西溪且留下”白墙与福堤，做地名记忆点",
     "白墙标语/福堤/老街", "正面平视；白墙做字幕底", "白天",
     "high", ["6a2bfab50000000007029b9f", "6a4b955e000000001102c06c"], ""),
    ("杭州", "hz-xixi", "西溪国家湿地公园", True, "西溪国家湿地公园", "detail", "shot",
     "莲花滩观鸟/水杉林：长焦拍鸟，逆光拍水杉与芦花",
     "候鸟/水杉/芦花", "长焦特写；逆光打透叶片", "秋冬候鸟季",
     "medium", ["6a4b955e000000001102c06c", "6a2bfab50000000007029b9f"], ""),
    ("杭州", "hz-xixi", "西溪国家湿地公园", True, "西溪国家湿地公园", "context", "narrative",
     "免费区 vs 收费区：核心美景多在免费区（湿地博物馆—莲花滩—高庄—河渚街—福堤）",
     "—", "—", "—",
     "high", ["6a2bfab50000000007029b9f", "6a4b955e000000001102c06c", "6a2faaa9000000000f015a1e"],
     "门票与免费范围以景区公告为准"),

    ("杭州", "hz-grandcanal", "京杭大运河·拱宸桥", True, "京杭大运河·拱宸桥", "landmark", "shot",
     "拱宸桥与桥西直街：石桥、运河、老厂房，历史街区定场",
     "拱宸桥/运河/老厂房", "桥头低机位；桥洞框景", "傍晚",
     "high", ["6a928cc1000000002a0320c6", "6a251b2f0000000015027d2d"], ""),
    ("杭州", "hz-grandcanal", "京杭大运河·拱宸桥", True, "京杭大运河·拱宸桥", "people", "shot",
     "桥西原住民与市井：巷弄门口、河边洗衣，非商业化细节",
     "巷弄/门口/居民", "中景跟拍；不摆拍", "清晨",
     "medium", ["6a928cc1000000002a0320c6", "6a44f8bd000000001c024d77"], ""),
    ("杭州", "hz-grandcanal", "京杭大运河·拱宸桥", True, "京杭大运河·拱宸桥", "night", "shot",
     "运河夜景与庙会灯会：鱼灯巡游、河面倒影（季节限定）",
     "鱼灯/河面/灯会", "低机位贴水面；跟拍巡游", "庙会期间（11月）",
     "medium", ["6af67afb000000002a02a89c", "6af2e0a0000000002b0107b4"], "庙会日期每年不同，以公告为准"),
    ("杭州", "hz-grandcanal", "京杭大运河·拱宸桥", True, "京杭大运河·拱宸桥", "route", "shot",
     "水路动线：香积寺→大兜路→小河直街→小河公园→拱宸桥，半天不走回头路",
     "香积寺/大兜路/小河直街", "沿河跟移；街口转场", "下午至傍晚",
     "high", ["6a251b2f0000000015027d2d", "6a3d0d05000000001c025fd7"], ""),

    ("杭州", "hz-qingminghetu", "良渚古城遗址", True, "良渚古城遗址", "season", "shot",
     "秋日稻田与芦苇：10-11月金黄稻浪、芒草抽穗，逆光有金边",
     "稻田/芒草/芦苇", "低机位逆光；大远景", "10-11月，15:00-17:00",
     "high", ["6abfdb7e000000000201138f", "6af6fe8e0000000009021e22", "6afad2a2000000002303f59c"], ""),
    ("杭州", "hz-qingminghetu", "良渚古城遗址", True, "良渚古城遗址", "landmark", "shot",
     "鹿苑小鹿：买鹿饼互动，草地与树荫做治愈点",
     "梅花鹿/草地/树荫", "低机位平视；长焦压缩", "夏季草木最绿",
     "high", ["6a3545f00000000007013d0b", "6a87e9b5000000000c02640c"], "鹿饼20元、观光车20元待核"),
    ("杭州", "hz-qingminghetu", "良渚古城遗址", True, "良渚古城遗址", "overlook", "shot",
     "莫角山高台：看遗址公园旷野，交代“五千年”尺度",
     "莫角山/旷野/远山", "高台大远景；航拍感", "晴天下午",
     "medium", ["6abfdb7e000000000201138f", "6a1a7487000000003700cfb9"], ""),
    ("杭州", "hz-qingminghetu", "良渚古城遗址", True, "良渚古城遗址", "context", "narrative",
     "遗址 vs 公园：园区空旷、遮阴少，观光车几乎必要；更适合当公园逛",
     "—", "—", "—",
     "medium", ["6a3545f00000000007013d0b", "6a940e5700000000240055a3"], "门票60元/市民免票待核"),

    # ---------------- 宁波 ----------------
    ("宁波", "nb-tianyige", "天一阁·月湖", True, "天一阁·月湖", "landmark", "shot",
     "宝书楼与天一池：藏书楼、假山与池水倒影，书香定场",
     "宝书楼/天一池/假山", "对称构图；池水倒影", "上午开门后",
     "high", ["6a6df2d60000000025010010", "6acb0578000000000100b8ef"], "门票30元待核"),
    ("宁波", "nb-tianyige", "天一阁·月湖", True, "天一阁·月湖", "detail", "shot",
     "秦氏支祠戏台与麻将起源馆：木雕藻井、戏台局部",
     "戏台/木雕/藻井", "仰拍藻井；局部特写", "白天",
     "medium", ["6acb0578000000000100b8ef", "6a2bd5e9000000001503f796"], "周一上午闭馆待核"),
    ("宁波", "nb-tianyige", "天一阁·月湖", True, "天一阁·月湖", "route", "shot",
     "单向动线：西大门→东明草堂→宝书楼→天一池→尊经阁→秦氏支祠→南园",
     "西大门/宝书楼/南园", "门洞框景；段落式移动", "2-3小时",
     "high", ["6acb0578000000000100b8ef", "6af2dd0b0000000022039b35"], ""),
    ("宁波", "nb-tianyige", "天一阁·月湖", True, "天一阁·月湖", "context", "narrative",
     "园林比藏书楼更出片；月湖免费且相邻，可串成半日",
     "—", "—", "—",
     "medium", ["6abcafba0000000018015b4a", "6a2e9c64000000001702882c", "6a6df2d60000000025010010"],
     "“月湖90分/天一阁6分”为主观评价，不引用为事实"),

    ("宁波", "nb-laowaitan", "老外滩", True, "老外滩", "night", "shot",
     "教堂与欧式建筑群夜景：三江口夜色、酒吧街灯光",
     "天主教堂/欧式立面/江景", "低机位仰拍教堂；江面倒影", "日落后",
     "high", ["6a4ca1160000000008033828", "6abb5652000000001a031e44", "6a911dd60000000007005eee"], ""),
    ("宁波", "nb-laowaitan", "老外滩", True, "老外滩", "landmark", "shot",
     "百年开埠建筑：江北天主教堂、旧领事馆与银行立面，白天看细节",
     "教堂/旧领事馆/立面", "正面平视；拱窗框景", "白天",
     "medium", ["6a7bd5d9000000000502a2e5", "6abb5652000000001a031e44"], ""),
    ("宁波", "nb-laowaitan", "老外滩", True, "老外滩", "context", "narrative",
     "老外滩主体在傍晚后；白天普通，夜景才是卖点，可与天一阁/月湖串一日",
     "—", "—", "—",
     "high", ["6a4ca1160000000008033828", "6abb5652000000001a031e44", "6a7bd5d9000000000502a2e5"], ""),

    ("宁波", "nb-dongqianhu", "东钱湖", True, "东钱湖", "light", "shot",
     "小加州日落/日出：南岸停车场，棕榈与镜面湖，粉紫渐变",
     "棕榈/湖面/日落", "低机位贴水面；逆光剪影", "日出前或日落后半小时",
     "high", ["6a83bf71000000002402fd3c", "6a1a7ce7000000003600298d", "6a64752200000000100265f8"],
     "“国内唯一/小加州”为笔记说法，不作事实"),
    ("宁波", "nb-dongqianhu", "东钱湖", True, "东钱湖", "route", "shot",
     "环湖小电驴：院士中心孤独的树→利民村→小济州岛→小加州→灯塔→小瑞士",
     "孤独的树/灯塔/湖岸", "广角环境；骑行跟拍", "晴天下午至日落",
     "high", ["6a2436b4000000001700882e", "6ab7914e00000000140034b7", "6a1c473a0000000036033f11"],
     "租车50元/天、押金200待核"),
    ("宁波", "nb-dongqianhu", "东钱湖", True, "东钱湖", "detail", "shot",
     "韩系咖啡与渔村巷弄：利民村、郭家峙，做“小济州岛”氛围",
     "咖啡店/渔村/巷弄", "窗框构图；中景", "下午",
     "medium", ["6a3e54490000000017028b1b", "6ace1571000000001500a216"], ""),
    ("宁波", "nb-dongqianhu", "东钱湖", True, "东钱湖", "context", "narrative",
     "环湖点位分散、公交少，自驾/电驴更合适；正午暴晒，日落时段最好",
     "—", "—", "—",
     "medium", ["6a2436b4000000001700882e", "6a1c473a0000000036033f11"], ""),

    # ---------------- 浅覆盖 8 市 ----------------
    ("温州", "wz-jiangxinyu", "江心屿", False, "江心屿", "landmark", "shot",
     "东西双塔与江心寺：渡轮登岛，双塔、古树与江面同框",
     "双塔/江心寺/古树", "渡轮上远景；登岛仰拍", "上午",
     "high", ["69633cf1000000000e00da95", "68124399000000002100f47b"], "门票30元/本地6元待核"),
    ("温州", "wz-jiangxinyu", "江心屿", False, "江心屿", "detail", "shot",
     "英国领事馆/宋园/西塔机位：红砖拱廊、榕树与江面",
     "红砖拱廊/榕树/西塔", "拱门框景；长焦特写", "下午",
     "medium", ["69633cf1000000000e00da95", "68e6673c0000000007009db3"], ""),
    ("温州", "wz-jiangxinyu", "江心屿", False, "江心屿", "context", "narrative",
     "轮渡班次与门票：8:00-20:00 每30分钟一班（待核）；岛上步行+观光车",
     "—", "—", "—",
     "medium", ["68124399000000002100f47b", "69633cf1000000000e00da95"], "班次与票价待核"),

    ("嘉兴", "jx-nanhu", "南湖", False, "南湖", "landmark", "shot",
     "湖心岛与烟雨楼：渡船上岛，红船与烟雨楼同框",
     "红船/烟雨楼/湖心岛", "船头低机位；楼阁对称", "上午",
     "high", ["6a75f2f9000000002500c817", "6a801aa0000000003400fb89", "6a222932000000000603012a"],
     "渡船20元/人待核"),
    ("嘉兴", "jx-nanhu", "南湖", False, "南湖", "water", "shot",
     "南湖水面与城市天际线：会景园码头看湖，船行水面",
     "湖面/游船/天际线", "水平线构图；长焦压缩", "晴天",
     "medium", ["6a222932000000000603012a", "6a75f2f9000000002500c817"], ""),
    ("嘉兴", "jx-nanhu", "南湖", False, "南湖", "context", "narrative",
     "景区免费、只收购船票；红船在湖心岛，别只在岸边拍",
     "—", "—", "—",
     "high", ["6a75f2f9000000002500c817", "6a801aa0000000003400fb89"], "船票与班次待核"),

    ("湖州", "huz-nanxun", "南浔古镇", True, "南浔古镇", "night", "shot",
     "入夜灯笼与河面倒影：百间楼、洪济桥一线，蓝调时刻最出片",
     "灯笼/河面/百间楼", "低机位贴水面；对称倒影", "17:00-18:30 亮灯",
     "high", ["6a7b0f52000000002202ee17", "6a26aa490000000006033c17", "6a331901000000002201a44e"], ""),
    ("湖州", "huz-nanxun", "南浔古镇", True, "南浔古镇", "landmark", "shot",
     "小莲庄/嘉业藏书楼/张石铭旧宅：园林、藏书楼与西式立面",
     "园林/藏书楼/西式立面", "门洞框景；建筑对称", "白天",
     "high", ["6a24a1af000000001702ff8e", "6a331901000000002201a44e"], "联票90元待核"),
    ("湖州", "huz-nanxun", "南浔古镇", True, "南浔古镇", "context", "narrative",
     "白天普通、夜里绝；联票不必全买，挑3-7处，南门进北门出",
     "—", "—", "—",
     "high", ["6a7b0f52000000002202ee17", "6a24a1af000000001702ff8e"], "“7处/3.5km”为笔记路线，非官方"),

    ("金华", "jh-hengdian", "横店影视城", False, "横店影视城", "landmark", "shot",
     "圆明新园与清明上河图：中西园林、宋式街市，两种片场风格对切",
     "圆明新园/宋式街市/宫殿", "对称构图；街市跟移", "白天",
     "medium", ["6a361f00000000000f006491", "6a6d95d7000000002500dff6"], "园区名称与开放范围待核"),
    ("金华", "jh-hengdian", "横店影视城", False, "横店影视城", "night", "shot",
     "清明上河图夜场：汴梁一梦、你好李清照演出与大宋坊夜市，NPC 互动",
     "夜场/演出/夜市", "舞台中景；灯笼街跟拍", "下午入园至夜场",
     "high", ["6a6d95d7000000002500dff6"], "演出场次以当日公告为准"),
    ("金华", "jh-hengdian", "横店影视城", False, "横店影视城", "context", "narrative",
     "横店是园区群不是单点；一天选1-2个园区，别贪多；演出时间决定动线",
     "—", "—", "—",
     "high", ["6a361f00000000000f006491", "6a6d95d7000000002500dff6"], "票价与套票待核"),

    ("衢州", "qz-jianglangshan", "江郎山", False, "江郎山", "landmark", "shot",
     "三爿石与一线天：丹霞石柱，晨雾或侧光，竖构图强调高度",
     "三爿石/一线天/丹霞", "竖构图仰拍；广角大远景", "清晨或雨后",
     "high", ["6a69bd5b0000000001032f91", "6ab640ed000000000100915b", "6ae099220000000034034e20"],
     "世界遗产/“国家地理”表述待核"),
    ("衢州", "qz-jianglangshan", "江郎山", False, "江郎山", "season", "shot",
     "山脚稻田与晒秋：10月稻浪、稻草人与远山，做秋日开场",
     "稻田/稻草人/远山", "低机位稻浪前景；逆光", "10月丰收季",
     "high", ["6ae099220000000034034e20", "6adde1e0000000002b01cacd"], "丰收季活动每年不同"),
    ("衢州", "qz-jianglangshan", "江郎山", False, "江郎山", "context", "narrative",
     "江郎山常与廿八都、清漾村打包两日；不要把廿八都画面标成江郎山",
     "—", "—", "—",
     "medium", ["6a6b41f1000000003400f1b9", "6ab640ed000000000100915b"], ""),

    ("舟山", "zs-putuoshan", "普陀山", False, "普陀山", "landmark", "shot",
     "南海观音、普济寺与佛顶山：黄墙拱门框景，观音像仰拍",
     "黄墙/拱门/观音像", "拱门框景；仰拍", "上午",
     "high", ["69df3e28000000001e00c263", "667929e7000000001e013de7"], "门票与船票待核"),
    ("舟山", "zs-putuoshan", "普陀山", False, "普陀山", "route", "shot",
     "祈福动线：码头→普济寺→法雨寺→慧济寺（佛顶山），公交/索道串联",
     "码头/寺院/索道", "段落式移动；索道航拍感", "9点前上岛",
     "medium", ["69df3e28000000001e00c263", "68b53fe0000000001b03cfaf"], "寺院顺序与交通待核"),
    ("舟山", "zs-putuoshan", "普陀山", False, "普陀山", "context", "narrative",
     "上岛成本：船票+门票套票、岛上公交/索道另收费；别把朱家尖当普陀山",
     "—", "—", "—",
     "medium", ["69df3e28000000001e00c263", "685fe2bb0000000023004dfb"], "220元套票待核"),

    ("台州", "tz-fucheng", "台州府城墙", False, "台州府城墙", "landmark", "shot",
     "揽胜门与城墙蜿蜒：登城俯拍东湖，城墙曲线做引导线",
     "城墙/揽胜门/东湖", "高机位俯拍；曲线引导线", "清晨或傍晚",
     "high", ["6ab8e226000000001203c35d", "6aeae7d6000000001600532e"], "门票60元待核"),
    ("台州", "tz-fucheng", "台州府城墙", False, "台州府城墙", "detail", "shot",
     "拱门框景与烟雨府城：拱门取景东湖与远山，雨天更江南",
     "拱门/东湖/远山", "拱门框景；雨天低饱和", "雨天/清晨",
     "high", ["6afae49d000000001f0151c9", "6ab8e226000000001203c35d"], ""),
    ("台州", "tz-fucheng", "台州府城墙", False, "台州府城墙", "context", "narrative",
     "揽胜门上坡198级台阶很陡；可从城隍庙上省体力；夜景值得住一晚",
     "—", "—", "—",
     "high", ["6aeae7d6000000001600532e", "6ab8e226000000001203c35d"], "台阶数字与门票待核"),

    ("丽水", "ls-xiandu", "仙都", False, "仙都", "landmark", "shot",
     "鼎湖峰：170米石柱与好溪倒影，清晨薄雾最像水墨",
     "鼎湖峰/好溪/倒影", "对称倒影；竖构图", "清晨6-7点",
     "high", ["6a800f4a000000003300cb07", "6aade93c000000002502e47e", "6a6ed9f7000000002403f964"],
     "170米/“天下第一笋”待核"),
    ("丽水", "ls-xiandu", "仙都", False, "仙都", "detail", "shot",
     "朱潭山石桥与老农牵牛：石板桥、烟雾与逆光，经典水墨构图",
     "石桥/耕牛/烟雾", "低机位长焦；逆光剪影", "清晨6-7点，春夏雨后",
     "high", ["6a6ed9f7000000002403f964", "6aade93c000000002502e47e", "6a9bcfc6000000002a0042ef"],
     "耕牛摆拍时间与联票150元待核"),
    ("丽水", "ls-xiandu", "仙都", False, "仙都", "context", "narrative",
     "景交车环线；不买联票也能走朱潭山、岩宕书房等点位",
     "—", "—", "—",
     "medium", ["6a9bcfc6000000002a0042ef", "6aba5488000000001a0204b0"], "免票范围与景交车票价待核"),

    # ---------------- 二次扩量的 16 个点位 ----------------
    ("温州", "wz-yandangshan", "雁荡山", False, "雁荡山", "landmark", "shot",
     "灵峰与方洞：山体剪影、洞窟与栈道，先给“三山五岳”的尺度",
     "灵峰/方洞/栈道", "竖构图仰拍山体；栈道做引导线", "晴天上午或雨后云雾",
     "high", ["6a4b9fc80000000011004c2e", "6a3ba7b200000000220181fd", "6a366c6b000000000f01d154"],
     "联票80元、景交10元待核"),
    ("温州", "wz-yandangshan", "雁荡山", False, "雁荡山", "water", "shot",
     "大龙湫瀑布：飞瀑深潭与铁索桥，做“夏日溯溪”的清凉段落",
     "瀑布/深潭/铁索桥", "低机位仰拍落差；慢门拉丝", "夏季水量大",
     "high", ["6a57017a0000000006032b25", "6a3ba7b200000000220181fd"], "“三绝之首”为主观排序"),
    ("温州", "wz-yandangshan", "雁荡山", False, "雁荡山", "context", "narrative",
     "雁荡山卖点是“看山不爬山”，两日可拆大龙湫+灵峰+方洞+灵岩；夜景另算",
     "—", "—", "—",
     "medium", ["6a4b9fc80000000011004c2e", "6a3ba7b200000000220181fd"], "各景区联票与夜场票价待核"),

    ("温州", "wz-nanxijiang", "楠溪江", False, "楠溪江", "water", "shot",
     "狮子岩与江面：竹筏、浅滩与倒影，做“江浙沪后花园”的开场",
     "狮子岩/江面/竹筏", "低机位贴水面；横向长卷", "秋季水清",
     "high", ["6ac5e2c60000000002011b0e", "6ab4f738000000001402e746"], ""),
    ("温州", "wz-nanxijiang", "楠溪江", False, "楠溪江", "detail", "shot",
     "碇步桥与古村：踩石墩过河、卵石墙与老屋，做慢节奏细节",
     "碇步桥/卵石墙/古村", "低机位拍石墩序列；中景人物过桥", "白天",
     "high", ["6a4a13e80000000017008fa4", "6ab4f738000000001402e746"], "永嘉书院门票50元待核"),
    ("温州", "wz-nanxijiang", "楠溪江", False, "楠溪江", "route", "shot",
     "三站动线：永嘉书院→陶公洞放生池→丽水古街，一天不走回头路",
     "书院/放生池/古街", "段落式移动；街口转场", "全天",
     "medium", ["6a4a13e80000000017008fa4", "6a3191390000000011011197"], ""),

    ("嘉兴", "jx-wuzhen", "乌镇", False, "乌镇", "night", "shot",
     "西栅夜灯：河道两岸灯火与水面倒影，蓝调时刻最稳",
     "灯笼/河道/倒影", "低机位贴水面；对称倒影", "日落后至21:30",
     "high", ["6aaeabb20000000011033cd2", "6ae8728d0000000034004837", "6af14da2000000003501fc37"],
     "西栅150元、东栅110元待核"),
    ("嘉兴", "jx-wuzhen", "乌镇", False, "乌镇", "detail", "shot",
     "草木染坊与昭明书院：蓝印花布垂挂、木构书院的纹理特写",
     "染布/书院/木构", "仰拍垂布；窗棂框景", "白天",
     "high", ["6af14da2000000003501fc37", "6add62b9000000002a011d06"], ""),
    ("嘉兴", "jx-wuzhen", "乌镇", False, "乌镇", "context", "narrative",
     "东西栅分开收费、西栅开到22点；只买一栅的话选西栅看夜景",
     "—", "—", "—",
     "high", ["6aaeabb20000000011033cd2", "6add62b9000000002a011d06"], "票价与闭园时间待核"),

    ("嘉兴", "jx-xitang", "西塘", False, "西塘", "light", "shot",
     "烟雨长廊与清晨薄雾：人少时的水乡本色，青石板与老屋",
     "长廊/薄雾/青石板", "长廊引导线；低饱和", "清晨开门前",
     "high", ["6ac85a7f00000000150123d3", "6aaa8343000000002700ac45"], "8点前免大门票待核"),
    ("嘉兴", "jx-xitang", "西塘", False, "西塘", "night", "shot",
     "入夜灯火：红灯笼沿河亮起，水面反光做情绪落点",
     "灯笼/河面/夜街", "低机位贴水面；暖调", "17点后",
     "high", ["6ac85a7f00000000150123d3", "6aabd2f1000000002900f59b"], ""),
    ("嘉兴", "jx-xitang", "西塘", False, "西塘", "context", "narrative",
     "商业化较重、招牌杂乱；清晨与夜里最好，白天偏吵",
     "—", "—", "—",
     "medium", ["6aaa8343000000002700ac45", "6aabd2f1000000002900f59b"], "免票时段以景区公告为准"),

    ("湖州", "huz-moganshan", "莫干山", True, "莫干山", "season", "shot",
     "秋日红叶：别墅前的枫树、银杏与烫金道路，季节限定",
     "红枫/银杏/别墅", "长焦压缩；逆光打透叶片", "10月底-11月中",
     "high", ["6afe91e20000000023011aeb", "6b002855000000002a01cfce", "6af9c28d0000000018026966"],
     "红叶期每年不同，以实时花讯为准"),
    ("湖州", "huz-moganshan", "莫干山", True, "莫干山", "detail", "shot",
     "山间别墅与咖啡馆：红房子白房子、观景台直面裸心堡",
     "别墅/观景台/咖啡", "建筑对称；窗框取景", "午后斜光",
     "medium", ["6ae7163400000000060310e5", "6af9c28d0000000018026966"], "店铺营业时间与门票待核"),
    ("湖州", "huz-moganshan", "莫干山", True, "莫干山", "context", "narrative",
     "山上商业关得早（18:30 前后），住山上是拍到晨昏的前提",
     "—", "—", "—",
     "medium", ["6ae7163400000000060310e5", "6b002855000000002a01cfce"], ""),

    ("湖州", "huz-taihu", "太湖旅游度假区", True, "太湖旅游度假区", "night", "shot",
     "月亮湾夜景：环形建筑灯光与水面倒影，蓝调半小时最出片",
     "环形建筑/湖面/灯光", "低机位贴水面；对称倒影", "19:00-19:30 蓝调",
     "high", ["6a368d82000000001101219b", "6aaa631c000000002901569b"], "喷泉场次与亮灯时间待核"),
    ("湖州", "huz-taihu", "太湖旅游度假区", True, "太湖旅游度假区", "water", "shot",
     "湖面与渔人码头：阴雨天的灰蓝层次，做“洱海平替”氛围",
     "湖面/码头/雨雾", "横向长卷；低饱和", "阴雨天",
     "medium", ["6a3fc44200000000160267f5", "6ae1b531000000002501fbce"], ""),
    ("湖州", "huz-taihu", "太湖旅游度假区", True, "太湖旅游度假区", "context", "narrative",
     "核心看夜景与喷泉；白天景观一般，阴雨天反而有氛围",
     "—", "—", "—",
     "medium", ["6a368d82000000001101219b", "6a3fc44200000000160267f5"], "“马桶圈”为网络俗称，不宜作正式表述"),

    ("金华", "jh-shuanglongdong", "双龙洞", False, "双龙洞", "landmark", "shot",
     "卧舟进洞：小船贴水面钻进洞口，还原课本里的“躺着进洞”",
     "洞口/小船/水面", "低机位贴水面；洞内仰拍", "上午人少",
     "high", ["69e627340000000023006696", "69f010d70000000038020174"], "景交40元待核"),
    ("金华", "jh-shuanglongdong", "双龙洞", False, "双龙洞", "interior", "shot",
     "洞内钟乳与暗河：灯光下的石幔、锦鲤与倒影",
     "钟乳/暗河/灯光", "洞内长曝；局部特写", "洞内恒温",
     "medium", ["69f010d70000000038020174", "69e62ea4000000001a0262c9"], ""),
    ("金华", "jh-shuanglongdong", "双龙洞", False, "双龙洞", "context", "narrative",
     "双龙洞是“双龙风景旅游区”的一个片区；叶圣陶《记金华的双龙洞》是叙事抓手",
     "—", "—", "—",
     "high", ["69e627340000000023006696", "69e62ea4000000001a0262c9"], "课文篇名与年级以现行教材为准"),

    ("金华", "jh-zhugecun", "诸葛八卦村", False, "诸葛八卦村", "overlook", "shot",
     "钟池与八卦布局：太极眼一半水一半陆，高处俯拍村落肌理",
     "钟池/巷道/马头墙", "高处俯拍；对称构图", "白天",
     "high", ["6ab9115b00000000180100bc", "6aba38530000000018011a34"], "“700年”“最大诸葛后裔聚居地”待核"),
    ("金华", "jh-zhugecun", "诸葛八卦村", False, "诸葛八卦村", "people", "shot",
     "巷弄日常：老人、门楼与斑驳墙面，避开摆拍感",
     "巷弄/门楼/居民", "中景跟拍；不摆拍", "清晨或傍晚",
     "medium", ["6aba38530000000018011a34", "6adb2219000000002d004f77"], ""),
    ("金华", "jh-zhugecun", "诸葛八卦村", False, "诸葛八卦村", "context", "narrative",
     "村内多次办联动活动（集章、生日月），画面里可能出现活动物料，需避让",
     "—", "—", "—",
     "medium", ["6abe3d68000000001500e25e", "6adb2219000000002d004f77"], "活动信息时效性强，不作事实引用"),

    ("衢州", "qz-nianbadu", "廿八都古镇", False, "廿八都古镇", "landmark", "shot",
     "四派建筑混搭：浙派、闽派、徽派、赣派同街，白墙黛瓦与马头墙",
     "马头墙/门楼/青石板", "正面平视；街巷纵深", "上午",
     "high", ["6ad0ec64000000000a025343", "6a3493190000000007023ad1"], "“国家地理认证”为笔记说法，不作事实"),
    ("衢州", "qz-nianbadu", "廿八都古镇", False, "廿八都古镇", "people", "shot",
     "本地人日常：拎菜篮的居民、老屋门口，非商业化的古镇质感",
     "居民/门廊/老街", "中景跟拍；不摆拍", "清晨",
     "high", ["6ad0ec64000000000a025343", "6abafa930000000018004332"], ""),
    ("衢州", "qz-nianbadu", "廿八都古镇", False, "廿八都古镇", "context", "narrative",
     "卖点是“没被商业化”，不要与乌镇西塘类比；三省交界、适合自驾串联江郎山",
     "—", "—", "—",
     "medium", ["6ad0ec64000000000a025343", "6abcca18000000001a033557"], ""),

    ("衢州", "qz-longyoushiku", "龙游石窟", False, "龙游石窟", "interior", "shot",
     "地下石窟：巨大石柱与凿痕，人工开凿的尺度感",
     "石柱/凿痕/洞窟", "广角仰拍；人物做尺度参照", "白天",
     "high", ["6afc8947000000001803bbbc", "6ae84d71000000001a021e20"], "开凿年代与用途无定论，不可断言"),
    ("衢州", "qz-longyoushiku", "龙游石窟", False, "龙游石窟", "context", "narrative",
     "“谁挖的、做什么用”至今没有定论；脚本应保留悬念，不要给结论",
     "—", "—", "—",
     "high", ["6afc8947000000001803bbbc", "6aa90e9c000000000b00ffd1"], "洞窟数量各说法不一，需核"),

    ("舟山", "zs-zhujiajian", "朱家尖", False, "朱家尖", "water", "shot",
     "大青山盘山公路与海湾：山海同框，自驾视角最出片",
     "盘山公路/海湾/沙滩", "高机位俯拍海湾；公路做引导线", "晴天",
     "high", ["6ae452b500000000270161d9", "6ad702db00000000280276aa"], "大青山门票待核"),
    ("舟山", "zs-zhujiajian", "朱家尖", False, "朱家尖", "detail", "shot",
     "樟州湾与小乌石塘：鹅卵石滩、渔村与海景公路",
     "鹅卵石滩/渔村/海景", "低机位拍石滩纹理；长焦压缩", "白天",
     "medium", ["6af98de2000000001802b6cc", "6afeee0c00000000090203dc"], ""),
    ("舟山", "zs-zhujiajian", "朱家尖", False, "朱家尖", "context", "narrative",
     "朱家尖是去普陀山的渡口所在（蜈蚣峙码头），可与普陀山串成两日",
     "—", "—", "—",
     "medium", ["6afeee0c00000000090203dc", "6ae452b500000000270161d9"], "码头班次待核"),

    ("舟山", "zs-dongjidao", "东极岛", False, "东极岛", "landmark", "shot",
     "庙子湖与东福山：海岛渔村、灯塔与礁石，国内最早海上日出",
     "渔村/灯塔/礁石", "航拍或高处俯拍；竖构图", "日出前后",
     "high", ["6a294332000000003501f958", "6a2948d6000000001502669b"], "“最早日出”表述需核"),
    ("舟山", "zs-dongjidao", "东极岛", False, "东极岛", "water", "shot",
     "海水由黄转蓝：船行途中与环岛海面，做“离岛”转场",
     "海面/船尾浪/远岛", "船尾长焦；水平线构图", "晴天上岛",
     "high", ["6a2948d6000000001502669b", "6a47973e000000001003dbfb"], ""),
    ("舟山", "zs-dongjidao", "东极岛", False, "东极岛", "context", "narrative",
     "上岛靠船、班次少且受天气影响；晕船药与现金是必备项",
     "—", "—", "—",
     "high", ["6a294332000000003501f958", "6a5af8d7000000001303e995"], "船班与票价待核"),

    ("台州", "tz-shenxianju", "神仙居", False, "神仙居", "overlook", "shot",
     "南天顶与云海：栈道悬空、云雾漫山，做“仙侠世界”大景",
     "栈道/云海/峰林", "高处广角；云雾天层次最好", "清晨或雨后",
     "high", ["6a7550d10000000028006dd3", "6a38ae650000000021015242", "6a320ad100000000220141e8"],
     "索道票价与运营时间待核"),
    ("台州", "tz-shenxianju", "神仙居", False, "神仙居", "detail", "shot",
     "南天桥与莲花台：玻璃桥面、悬挑平台，人物做尺度参照",
     "南天桥/莲花台/栈道", "低机位仰拍；桥面透视", "白天",
     "medium", ["6a38ae650000000021015242", "6a68704200000000130277e9"], ""),
    ("台州", "tz-shenxianju", "神仙居", False, "神仙居", "context", "narrative",
     "南门进、索道上山是省力路线；下索道先走佛影莲韵再上南天顶",
     "—", "—", "—",
     "medium", ["6a38ae650000000021015242", "6a320ad100000000220141e8"], "索道与徒步耗时待核"),

    ("台州", "tz-shitang", "石塘", False, "石塘", "overlook", "shot",
     "麒麟山俯拍七彩小箬村：马卡龙色房屋层叠到海边",
     "彩色房屋/海湾/山顶", "山顶俯拍全景；竖构图", "日落前",
     "high", ["68ebbef50000000004002a2c", "6996dcfa000000001a01c9d3"], "停车30元/天待核"),
    ("台州", "tz-shitang", "石塘", False, "石塘", "people", "shot",
     "渔村日常：晒鱼鲞、整理渔网、石砌老房，彩色之下的真实生活",
     "渔网/鱼鲞/石屋", "中景跟拍；局部特写", "清晨",
     "high", ["6a82fa580000000008013223", "69f38c66000000001a034bf2"], ""),
    ("台州", "tz-shitang", "石塘", False, "石塘", "context", "narrative",
     "小箬村只是石塘的一个村；对戒观景台、千年曙光碑是另外的点位",
     "—", "—", "—",
     "medium", ["68ebbef50000000004002a2c", "69f38c66000000001a034bf2"], ""),

    ("丽水", "ls-yunhetitian", "云和梯田", False, "云和梯田", "overlook", "shot",
     "九曲云环日出云海：梯田层叠到山顶，日出前半小时到位",
     "梯田/云海/日出", "高处广角；横向长卷", "日出前30分钟",
     "high", ["6ae9a2ee000000000e034dca", "6aeea0a5000000001603e5b4", "6aedcd99000000002b011d54"],
     "门票80元、套票200元待核"),
    ("丽水", "ls-yunhetitian", "云和梯田", False, "云和梯田", "season", "shot",
     "稻黄与灌水两季：10-11月金浪，割后灌水反而更出片",
     "稻浪/水面反光/田埂", "低机位稻浪前景；逆光金边", "10-11月",
     "high", ["6afb9821000000002b010914", "6aedcd99000000002b011d54"], ""),
    ("丽水", "ls-yunhetitian", "云和梯田", False, "云和梯田", "context", "narrative",
     "导航搜“云和梯田”会送到山下游客中心，观景台还有十几公里；改搜“九曲云环”或“崇头镇”",
     "—", "—", "—",
     "high", ["6aedcd99000000002b011d54", "6aeea0a5000000001603e5b4"], ""),

    ("丽水", "ls-songyang", "松阳古村落", False, "松阳古村落", "landmark", "shot",
     "杨家堂金色夯土墙：夕阳下的黄泥墙与层叠屋顶",
     "夯土墙/屋顶/古树", "侧逆光；长焦压缩层次", "傍晚",
     "high", ["6abf2473000000001b02cb93", "6ae9ceef000000000e02f34e"], "各古村均免费待核"),
    ("丽水", "ls-songyang", "松阳古村落", False, "松阳古村落", "detail", "shot",
     "陈家铺悬崖书店与石拱桥：文艺空间与百年石桥的对比",
     "书店/石拱桥/竹海", "窗框取景；桥洞框景", "白天",
     "medium", ["6aaaa8c0000000002902d2d7", "6b017a7d000000002a029f7e"], "书店营业状态待核"),
    ("丽水", "ls-songyang", "松阳古村落", False, "松阳古村落", "context", "narrative",
     "松阳是一组古村（杨家堂/陈家铺/松庄/西坑/平田），村间需自驾；风景相似，不必全去",
     "—", "—", "—",
     "high", ["6abf2473000000001b02cb93", "6aaaa8c0000000002902d2d7"], "村间车程待核"),
]

# 七城 KB 已补齐：这 21 个点位实体已入 data_zhejiang_kb，NEW_ROWS 里的 in_kb 统一翻真。
SEVEN_CITY_SPOT_IDS = {
    "wz-yandangshan", "wz-nanxijiang", "wz-jiangxinyu",
    "jx-wuzhen", "jx-nanhu", "jx-xitang",
    "jh-shuanglongdong", "jh-hengdian", "jh-zhugecun",
    "qz-nianbadu", "qz-jianglangshan", "qz-longyoushiku",
    "zs-dongjidao", "zs-putuoshan", "zs-zhujiajian",
    "tz-fucheng", "tz-shitang", "tz-shenxianju",
    "ls-yunhetitian", "ls-xiandu", "ls-songyang",
}
NEW_ROWS = [
    (row[0], row[1], row[2], True, *row[4:]) if row[1] in SEVEN_CITY_SPOT_IDS else row
    for row in NEW_ROWS
]


def main() -> int:
    with NOTES.open(encoding="utf-8-sig") as handle:
        notes = list(csv.DictReader(handle))
    known_ids = {row["note_id"] for row in notes}
    kb_ids = set()
    for name in ("hangzhou.json", "ningbo.json", "shaoxing.json", "huzhou.json",
                 "wenzhou.json", "jiaxing.json", "jinhua.json", "quzhou.json",
                 "zhoushan.json", "taizhou.json", "lishui.json"):
        data = json.loads((KB_DIR / name).read_text(encoding="utf-8"))
        kb_ids.update(entity["id"] for entity in data["entities"])

    rows = []
    errors = []
    with SHAOXING.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            in_kb = str(row["in_kb"]).strip().lower() == "true"
            rows.append({
                "city": "绍兴", "entity_id": row["entity_id"], "entity_name": row["entity_name"],
                "in_kb": in_kb, "spot": row["entity_name"], "shot_tag": row["shot_tag"],
                "angle_type": row["angle_type"], "intent": row["intent"], "motif": row["motif"],
                "composition": row["composition"], "best_window": row["best_window"],
                "confidence": row["confidence"],
                "support_note_ids": [i for i in row["support_note_ids"].split(";") if i],
                "claim_risk": row["claim_risk"],
            })
    for item in NEW_ROWS:
        (city, entity_id, entity_name, in_kb, spot, tag, angle_type, intent, motif,
         composition, best_window, confidence, note_ids, claim) = item
        rows.append({
            "city": city, "entity_id": entity_id, "entity_name": entity_name,
            "in_kb": in_kb, "spot": spot, "shot_tag": tag, "angle_type": angle_type,
            "intent": intent, "motif": motif, "composition": composition,
            "best_window": best_window, "confidence": confidence,
            "support_note_ids": note_ids, "claim_risk": claim,
        })

    for row in rows:
        if row["in_kb"] and row["entity_id"] not in kb_ids:
            errors.append(f"{row['entity_id']}: in_kb but missing from KB")
        if not row["in_kb"] and row["entity_id"] in kb_ids:
            errors.append(f"{row['entity_id']}: not in KB but id exists")
        if row["shot_tag"] not in TAGS:
            errors.append(f"{row['entity_id']}: bad tag {row['shot_tag']}")
        if row["angle_type"] not in {"shot", "narrative"}:
            errors.append(f"{row['entity_id']}: bad angle_type")
        if row["angle_type"] == "shot" and not row["support_note_ids"]:
            errors.append(f"{row['entity_id']}/{row['shot_tag']}: no note support")
        for note_id in row["support_note_ids"]:
            if note_id not in known_ids:
                errors.append(f"{row['entity_id']}: note {note_id} not in table")
        if row["spot"] not in SLUGS:
            errors.append(f"{row['spot']}: no contact sheet slug")
    if errors:
        print("FAILED")
        for error in errors:
            print(" -", error)
        return 1

    rows.sort(key=lambda row: CITY_ORDER.index(row["city"]))
    columns = ["city", "entity_id", "entity_name", "in_kb", "spot", "shot_tag",
               "angle_type", "intent", "motif", "composition", "best_window",
               "confidence", "support_note_ids", "support_count", "claim_risk",
               "contact_sheet", "status"]
    with OUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "city": row["city"], "entity_id": row["entity_id"],
                "entity_name": row["entity_name"], "in_kb": str(row["in_kb"]),
                "spot": row["spot"], "shot_tag": row["shot_tag"],
                "angle_type": row["angle_type"], "intent": row["intent"],
                "motif": row["motif"], "composition": row["composition"],
                "best_window": row["best_window"], "confidence": row["confidence"],
                "support_note_ids": ";".join(row["support_note_ids"]),
                "support_count": len(row["support_note_ids"]),
                "claim_risk": row["claim_risk"],
                "contact_sheet": f"runtime/_xhs_images/contact_sheets/{row['city']}-{SLUGS[row['spot']]}.jpg",
                "status": "reference-only",
            })
    print(json.dumps({
        "rows": len(rows),
        "cities": len({row["city"] for row in rows}),
        "entities": len({row["entity_id"] for row in rows}),
        "in_kb": sum(1 for row in rows if row["in_kb"]),
        "high": sum(1 for row in rows if row["confidence"] == "high"),
        "out": str(OUT),
    }, ensure_ascii=False))
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
