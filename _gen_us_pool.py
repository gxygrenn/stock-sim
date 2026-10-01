# -*- coding: utf-8 -*-
"""生成 100 只美股 DEFAULT_POOL 代码并替换 server.py。"""
import io, re

# code: (name, alias, tags, beta)
US = [
    ("usAAPL", "苹果", "Apple,苹果公司", "消费电子,手机,iPhone,MR,果链,智能穿戴", 1.0),
    ("usNVDA", "英伟达", "NVIDIA,英伟达公司", "AI芯片,GPU,算力,半导体,数据中心,人工智能", 1.7),
    ("usMSFT", "微软", "Microsoft,微软公司", "云计算,Azure,人工智能,AI,办公软件,OpenAI", 0.9),
    ("usGOOGL", "谷歌", "Google,Alphabet,谷歌公司", "搜索,人工智能,AI,云计算,广告,YouTube", 1.1),
    ("usMETA", "Meta", "脸书,Facebook,Meta平台", "社交媒体,元宇宙,人工智能,AI,广告,Instagram", 1.4),
    ("usAMZN", "亚马逊", "Amazon,亚马逊公司", "电商,云计算,AWS,物流,人工智能", 1.1),
    ("usTSLA", "特斯拉", "Tesla,特斯拉公司", "新能源车,电动车,自动驾驶,储能,机器人,马斯克", 1.9),
    ("usTSM", "台积电", "TSMC,台积电公司", "半导体,晶圆代工,芯片,先进制程", 1.2),
    ("usAVGO", "博通", "Broadcom,博通公司", "半导体,芯片,AI芯片,网络,云计算", 1.5),
    ("usAMD", "超威半导体", "AMD,超威", "半导体,CPU,GPU,AI芯片,芯片,数据中心", 1.8),
    ("usINTC", "英特尔", "Intel,英特尔公司", "半导体,CPU,芯片,晶圆,代工", 1.4),
    ("usNFLX", "奈飞", "Netflix,网飞", "流媒体,视频,订阅,影视,内容", 1.3),
    ("usQCOM", "高通", "Qualcomm,高通公司", "半导体,芯片,5G,手机芯片,通信", 1.3),
    ("usTXN", "德州仪器", "TI,德州仪器公司", "半导体,模拟芯片,芯片,工业芯片", 1.0),
    ("usAMAT", "应用材料", "AMAT,应用材料公司", "半导体设备,芯片,晶圆,制造设备", 1.5),
    ("usLRCX", "泛林集团", "Lam Research,拉姆研究", "半导体设备,芯片,晶圆,刻蚀", 1.5),
    ("usMU", "美光科技", "Micron,美光", "半导体,存储芯片,内存,DRAM,HBM", 1.8),
    ("usORCL", "甲骨文", "Oracle,甲骨文公司", "云计算,数据库,企业软件,AI", 1.2),
    ("usCRM", "赛富时", "Salesforce,客户关系管理", "云计算,SaaS,企业软件,CRM,AI", 1.3),
    ("usADBE", "Adobe", "奥多比,Adobe公司", "软件,创意软件,AI,Photoshop,设计", 1.3),
    ("usIBM", "IBM", "国际商业机器,IBM公司", "云计算,人工智能,AI,企业服务,量子计算", 0.9),
    ("usCSCO", "思科", "Cisco,思科系统", "网络设备,通信,网络安全,企业网络", 0.8),
    ("usPLTR", "Palantir", "帕兰提尔,Palantir公司", "大数据,AI,数据分析,国防,软件", 2.0),
    ("usSMCI", "超微电脑", "Super Micro,超微", "服务器,AI服务器,数据中心,算力,液冷", 2.2),
    ("usINTU", "财捷", "Intuit,直觉公司", "软件,财务软件,报税,SaaS", 1.0),
    ("usUBER", "优步", "Uber,优步科技", "出行,网约车,外卖,自动驾驶", 1.5),
    ("usABNB", "爱彼迎", "Airbnb,民宿", "旅游,住宿,共享经济,出行", 1.5),
    ("usPYPL", "Paypal", "贝宝,Paypal公司", "支付,金融科技,在线支付,移动支付", 1.4),
    ("usCOIN", "Coinbase", "数字货币交易所,加密交易", "加密货币,比特币,区块链,交易所", 2.5),
    ("usJPM", "摩根大通", "JPMorgan,小摩", "银行,金融,投行,大金融", 0.8),
    ("usBAC", "美国银行", "Bank of America,美银", "银行,金融,大金融,信贷", 0.9),
    ("usWFC", "富国银行", "Wells Fargo", "银行,金融,大金融,信贷", 0.8),
    ("usGS", "高盛", "Goldman Sachs,高盛集团", "投行,金融,证券,资管", 1.0),
    ("usMS", "摩根士丹利", "Morgan Stanley,大摩", "投行,金融,证券,财富管理", 1.0),
    ("usV", "Visa", "维萨,Visa公司", "支付,信用卡,金融,清算", 0.7),
    ("usMA", "万事达", "Mastercard,万事达卡", "支付,信用卡,金融,清算", 0.8),
    ("usBLK", "贝莱德", "BlackRock,黑岩", "资管,基金,金融,ETF", 0.9),
    ("usAXP", "美国运通", "American Express,运通", "支付,信用卡,金融,消费金融", 0.9),
    ("usC", "花旗集团", "Citigroup,花旗", "银行,金融,大金融,投行", 1.0),
    ("usJNJ", "强生", "Johnson & Johnson,强生公司", "医药,医疗,制药,消费品", 0.6),
    ("usLLY", "礼来", "Eli Lilly,礼来公司", "医药,制药,减肥药,糖尿病,创新药", 1.1),
    ("usUNH", "联合健康", "UnitedHealth,联合健康集团", "医疗保险,医疗,健康管理,保险", 0.7),
    ("usPFE", "辉瑞", "Pfizer,辉瑞公司", "医药,制药,疫苗,生物医药", 0.8),
    ("usMRK", "默沙东", "Merck,默克", "医药,制药,创新药,疫苗", 0.7),
    ("usABBV", "艾伯维", "AbbVie,艾伯维公司", "医药,制药,生物医药,免疫", 0.8),
    ("usABT", "雅培", "Abbott,雅培公司", "医疗,诊断,医疗器械,营养", 0.6),
    ("usNVO", "诺和诺德", "Novo Nordisk,诺和诺德公司", "医药,减肥药,糖尿病,制药", 1.0),
    ("usTMO", "赛默飞世尔", "Thermo Fisher,赛默飞", "医疗,生命科学,诊断,仪器", 0.9),
    ("usBMY", "施贵宝", "Bristol Myers,百时美施贵宝", "医药,制药,肿瘤,创新药", 0.8),
    ("usAMGN", "安进", "Amgen,安进公司", "医药,生物医药,制药,创新药", 0.7),
    ("usGILD", "吉利德科学", "Gilead,吉利德", "医药,制药,抗病毒,生物医药", 0.8),
    ("usMRNA", "莫德纳", "Moderna,莫德纳公司", "医药,疫苗,mRNA,生物医药", 2.0),
    ("usKO", "可口可乐", "Coca-Cola,可乐", "消费,饮料,食品,大消费", 0.5),
    ("usPEP", "百事可乐", "PepsiCo,百事", "消费,饮料,食品,零食,大消费", 0.5),
    ("usPG", "宝洁", "P&G,宝洁公司", "消费,日化,日用消费品,大消费", 0.5),
    ("usWMT", "沃尔玛", "Walmart,沃尔玛公司", "零售,商超,消费,大消费", 0.5),
    ("usCOST", "好市多", "Costco,开市客", "零售,会员店,商超,消费", 0.5),
    ("usMCD", "麦当劳", "McDonald's,麦当劳公司", "餐饮,快餐,消费,连锁", 0.5),
    ("usSBUX", "星巴克", "Starbucks,星巴克公司", "餐饮,咖啡,消费,连锁", 0.8),
    ("usNKE", "耐克", "Nike,耐克公司", "消费,服装,运动,鞋服,大消费", 0.8),
    ("usDIS", "迪士尼", "Disney,华特迪士尼", "娱乐,影视,主题公园,流媒体,IP", 1.0),
    ("usHD", "家得宝", "Home Depot,家得宝公司", "零售,家居,建材,消费", 0.8),
    ("usLOW", "劳氏", "Lowe's,劳氏公司", "零售,家居,建材,消费", 0.8),
    ("usTGT", "塔吉特", "Target,塔吉特公司", "零售,商超,消费,大消费", 0.8),
    ("usMELI", "美客多", "MercadoLibre,拉美电商", "电商,拉美,支付,金融科技", 1.5),
    ("usXOM", "埃克森美孚", "Exxon Mobil,美孚", "能源,石油,油气,化工", 0.9),
    ("usCVX", "雪佛龙", "Chevron,雪佛龙公司", "能源,石油,油气,化工", 0.9),
    ("usCOP", "康菲石油", "ConocoPhillips,康菲", "能源,石油,油气,资源", 1.0),
    ("usCAT", "卡特彼勒", "Caterpillar,卡特彼勒公司", "工业,工程机械,重工,设备", 1.1),
    ("usBA", "波音", "Boeing,波音公司", "航空,飞机制造,国防,工业", 1.3),
    ("usHON", "霍尼韦尔", "Honeywell,霍尼韦尔公司", "工业,航空,自动化,多元制造", 0.8),
    ("usGE", "GE航天航空", "General Electric,通用电气", "航空,发动机,工业,能源", 1.2),
    ("usMMM", "3M", "3M公司", "工业,化工,材料,多元制造", 0.9),
    ("usDE", "迪尔股份", "Deere,约翰迪尔", "农业机械,工业,农机,设备", 0.9),
    ("usLMT", "洛克希德马丁", "Lockheed Martin,洛马", "国防,军工,航空航天,军火", 0.7),
    ("usRTX", "雷神技术", "Raytheon,雷神", "国防,军工,航空航天,发动机", 0.9),
    ("usUPS", "联合包裹", "UPS,联合包裹服务", "物流,快递,运输,供应链", 0.8),
    ("usFDX", "联邦快递", "FedEx,联邦快递公司", "物流,快递,运输,供应链", 1.0),
    ("usLIN", "林德", "Linde,林德集团", "工业气体,化工,材料", 0.7),
    ("usT", "美国电话电报", "AT&T,美国电话电报公司", "电信,通信,运营商,5G", 0.6),
    ("usVZ", "威瑞森通信", "Verizon,威瑞森", "电信,通信,运营商,5G", 0.6),
    ("usTMUS", "T-Mobile", "T-Mobile美国", "电信,通信,运营商,5G", 0.7),
    ("usCMCSA", "康卡斯特", "Comcast,康卡斯特公司", "电信,媒体,有线电视,宽带", 0.7),
    ("usBABA", "阿里巴巴", "阿里,Alibaba", "电商,云计算,人工智能,AI,中概股", 1.5),
    ("usPDD", "拼多多", "PDD,Temu", "电商,中概股,海外电商,低价", 1.6),
    ("usJD", "京东", "JD,京东商城", "电商,物流,中概股,零售", 1.2),
    ("usBIDU", "百度", "Baidu,百度公司", "搜索,人工智能,AI,自动驾驶,中概股", 1.4),
    ("usNTES", "网易", "NetEase,网易公司", "游戏,中概股,互联网,音乐", 1.2),
    ("usTME", "腾讯音乐", "Tencent Music,腾讯音乐娱乐", "音乐,流媒体,中概股,娱乐", 1.3),
    ("usBILI", "哔哩哔哩", "Bilibili,B站", "视频,游戏,中概股,社区,二次元", 1.6),
    ("usNIO", "蔚来", "NIO,蔚来汽车", "新能源车,电动车,中概股,智能驾驶", 2.0),
    ("usXPEV", "小鹏", "Xpeng,小鹏汽车", "新能源车,电动车,中概股,智能驾驶", 2.0),
    ("usLI", "理想汽车", "Li Auto,理想", "新能源车,电动车,中概股,增程", 1.8),
    ("usFUTU", "富途控股", "Futu,富途", "券商,中概股,金融科技,互联网券商", 1.8),
    ("usTAL", "好未来", "TAL Education,学而思", "教育,中概股,在线教育,AI教育", 1.5),
    ("usEDU", "新东方", "New Oriental,新东方教育", "教育,中概股,留学,直播电商", 1.5),
    ("usSHOP", "Shopify", "Shopify公司,电商平台", "电商,SaaS,云计算,跨境电商", 1.8),
    ("usSPOT", "Spotify", "声田,Spotify公司", "音乐,流媒体,音频,订阅", 1.4),
    ("usDASH", "DoorDash", "DoorDash公司,外卖", "外卖,配送,餐饮,共享经济", 1.6),
    ("usAFRM", "Affirm", "Affirm公司,先买后付", "金融科技,消费金融,支付,先买后付", 2.2),
    ("usNET", "Cloudflare", "科赋锐,Cloudflare公司", "云计算,网络安全,CDN,边缘计算", 1.8),
    ("usCRWD", "CrowdStrike", "CrowdStrike公司,网络安全", "网络安全,云安全,软件,AI安全", 1.8),
]

lines = []
lines.append("# name 股票名 / alias 简称 / tags 板块·主题关键词（新闻命中这些词也会触发该股）")
lines.append("# 美股版：代码用 us 前缀，中文名/别名/标签用于匹配中文财经快讯")
lines.append("DEFAULT_POOL = [")
for code, name, alias, tags, beta in US:
    lines.append('    {"name": "%s", "code": "%s", "alias": "%s", "beta": %.1f,' % (name, code, alias, beta))
    lines.append('     "tags": "%s"},' % tags)
lines.append("]")
new_block = "\n".join(lines)

p = "server.py"
src = io.open(p, encoding="utf-8").read()
# 替换从 "# name 股票名" 到 "]" 结束的 DEFAULT_POOL 块
pat = re.compile(r'# name 股票名.*?\nDEFAULT_POOL = \[.*?\n\]', re.S)
src2, n = pat.subn(lambda m: new_block, src, count=1)
io.open(p, "w", encoding="utf-8").write(src2)
print("替换块数:", n)
print("股票池条数:", len(US))
