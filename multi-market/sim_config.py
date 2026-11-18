# -*- coding: utf-8 -*-
"""词库与三账户配置模板（纯数据）。由 server.py 装配注入共享命名空间。"""

import json
import os
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import server  # noqa: E402


# ---------------------------------------------------------------- 词库
GOOD = [
    "超预期", "预增", "预盈", "扭亏", "业绩增长", "净利润增长", "营收增长", "增长", "大涨", "涨停",
    "新高", "突破", "中标", "签约", "订单", "扩产", "涨价", "提价", "回购", "增持", "分红",
    "派息", "获批", "核准", "通过审核", "注册生效", "战略合作", "合作", "投资", "看好", "上调",
    "买入评级", "增持评级", "扶持", "补贴", "减税", "降准", "降息", "宽松", "刺激", "回暖",
    "复苏", "满产", "供不应求", "缺货", "斩获", "拿下", "落地", "投产", "量产", "交付", "翻倍",
    "盈利", "利好", "领涨", "走强", "反弹", "拉升", "溢价", "抢购", "售罄", "参股", "控股",
]

BAD = [
    "预减", "预亏", "亏损", "下滑", "下降", "大跌", "跌停", "暴跌", "新低", "跌破", "减持",
    "套现", "退市", "立案", "调查", "处罚", "监管", "问询", "警示", "风险", "诉讼", "仲裁",
    "冻结", "质押", "爆雷", "违约", "裁员", "停产", "减产", "召回", "降价", "下调", "看空",
    "做空", "不及预期", "低于预期", "商誉", "减值", "亏损扩大", "被查", "制裁", "关税",
    "限制", "禁令", "事故", "利空", "领跌", "走弱", "跳水", "下挫", "萎靡", "萎缩", "收缩",
    "终止", "中止", "失败", "流拍", "撤回", "取消", "解约",
]


US_POOL = [
    {"name": "苹果", "code": "usAAPL", "alias": "Apple,苹果公司", "beta": 1.0,
     "tags": "消费电子,手机,iPhone,MR,果链,智能穿戴"},
    {"name": "英伟达", "code": "usNVDA", "alias": "NVIDIA,英伟达公司", "beta": 1.7,
     "tags": "AI芯片,GPU,算力,半导体,数据中心,人工智能"},
    {"name": "微软", "code": "usMSFT", "alias": "Microsoft,微软公司", "beta": 0.9,
     "tags": "云计算,Azure,人工智能,AI,办公软件,OpenAI"},
    {"name": "谷歌", "code": "usGOOGL", "alias": "Google,Alphabet,谷歌公司", "beta": 1.1,
     "tags": "搜索,人工智能,AI,云计算,广告,YouTube"},
    {"name": "Meta", "code": "usMETA", "alias": "脸书,Facebook,Meta平台", "beta": 1.4,
     "tags": "社交媒体,元宇宙,人工智能,AI,广告,Instagram"},
    {"name": "亚马逊", "code": "usAMZN", "alias": "Amazon,亚马逊公司", "beta": 1.1,
     "tags": "电商,云计算,AWS,物流,人工智能"},
    {"name": "特斯拉", "code": "usTSLA", "alias": "Tesla,特斯拉公司", "beta": 1.9,
     "tags": "新能源车,电动车,自动驾驶,储能,机器人,马斯克"},
    {"name": "台积电", "code": "usTSM", "alias": "TSMC,台积电公司", "beta": 1.2,
     "tags": "半导体,晶圆代工,芯片,先进制程"},
    {"name": "博通", "code": "usAVGO", "alias": "Broadcom,博通公司", "beta": 1.5,
     "tags": "半导体,芯片,AI芯片,网络,云计算"},
    {"name": "超威半导体", "code": "usAMD", "alias": "AMD,超威", "beta": 1.8,
     "tags": "半导体,CPU,GPU,AI芯片,芯片,数据中心"},
    {"name": "英特尔", "code": "usINTC", "alias": "Intel,英特尔公司", "beta": 1.4,
     "tags": "半导体,CPU,芯片,晶圆,代工"},
    {"name": "奈飞", "code": "usNFLX", "alias": "Netflix,网飞", "beta": 1.3,
     "tags": "流媒体,视频,订阅,影视,内容"},
    {"name": "高通", "code": "usQCOM", "alias": "Qualcomm,高通公司", "beta": 1.3,
     "tags": "半导体,芯片,5G,手机芯片,通信"},
    {"name": "德州仪器", "code": "usTXN", "alias": "TI,德州仪器公司", "beta": 1.0,
     "tags": "半导体,模拟芯片,芯片,工业芯片"},
    {"name": "应用材料", "code": "usAMAT", "alias": "AMAT,应用材料公司", "beta": 1.5,
     "tags": "半导体设备,芯片,晶圆,制造设备"},
    {"name": "泛林集团", "code": "usLRCX", "alias": "Lam Research,拉姆研究", "beta": 1.5,
     "tags": "半导体设备,芯片,晶圆,刻蚀"},
    {"name": "美光科技", "code": "usMU", "alias": "Micron,美光", "beta": 1.8,
     "tags": "半导体,存储芯片,内存,DRAM,HBM"},
    {"name": "甲骨文", "code": "usORCL", "alias": "Oracle,甲骨文公司", "beta": 1.2,
     "tags": "云计算,数据库,企业软件,AI"},
    {"name": "赛富时", "code": "usCRM", "alias": "Salesforce,客户关系管理", "beta": 1.3,
     "tags": "云计算,SaaS,企业软件,CRM,AI"},
    {"name": "Adobe", "code": "usADBE", "alias": "奥多比,Adobe公司", "beta": 1.3,
     "tags": "软件,创意软件,AI,Photoshop,设计"},
    {"name": "IBM", "code": "usIBM", "alias": "国际商业机器,IBM公司", "beta": 0.9,
     "tags": "云计算,人工智能,AI,企业服务,量子计算"},
    {"name": "思科", "code": "usCSCO", "alias": "Cisco,思科系统", "beta": 0.8,
     "tags": "网络设备,通信,网络安全,企业网络"},
    {"name": "Palantir", "code": "usPLTR", "alias": "帕兰提尔,Palantir公司", "beta": 2.0,
     "tags": "大数据,AI,数据分析,国防,软件"},
    {"name": "超微电脑", "code": "usSMCI", "alias": "Super Micro,超微", "beta": 2.2,
     "tags": "服务器,AI服务器,数据中心,算力,液冷"},
    {"name": "财捷", "code": "usINTU", "alias": "Intuit,直觉公司", "beta": 1.0,
     "tags": "软件,财务软件,报税,SaaS"},
    {"name": "优步", "code": "usUBER", "alias": "Uber,优步科技", "beta": 1.5,
     "tags": "出行,网约车,外卖,自动驾驶"},
    {"name": "爱彼迎", "code": "usABNB", "alias": "Airbnb,民宿", "beta": 1.5,
     "tags": "旅游,住宿,共享经济,出行"},
    {"name": "Paypal", "code": "usPYPL", "alias": "贝宝,Paypal公司", "beta": 1.4,
     "tags": "支付,金融科技,在线支付,移动支付"},
    {"name": "Coinbase", "code": "usCOIN", "alias": "数字货币交易所,加密交易", "beta": 2.5,
     "tags": "加密货币,比特币,区块链,交易所"},
    {"name": "摩根大通", "code": "usJPM", "alias": "JPMorgan,小摩", "beta": 0.8,
     "tags": "银行,金融,投行,大金融"},
    {"name": "美国银行", "code": "usBAC", "alias": "Bank of America,美银", "beta": 0.9,
     "tags": "银行,金融,大金融,信贷"},
    {"name": "富国银行", "code": "usWFC", "alias": "Wells Fargo", "beta": 0.8,
     "tags": "银行,金融,大金融,信贷"},
    {"name": "高盛", "code": "usGS", "alias": "Goldman Sachs,高盛集团", "beta": 1.0,
     "tags": "投行,金融,证券,资管"},
    {"name": "摩根士丹利", "code": "usMS", "alias": "Morgan Stanley,大摩", "beta": 1.0,
     "tags": "投行,金融,证券,财富管理"},
    {"name": "Visa", "code": "usV", "alias": "维萨,Visa公司", "beta": 0.7,
     "tags": "支付,信用卡,金融,清算"},
    {"name": "万事达", "code": "usMA", "alias": "Mastercard,万事达卡", "beta": 0.8,
     "tags": "支付,信用卡,金融,清算"},
    {"name": "贝莱德", "code": "usBLK", "alias": "BlackRock,黑岩", "beta": 0.9,
     "tags": "资管,基金,金融,ETF"},
    {"name": "美国运通", "code": "usAXP", "alias": "American Express,运通", "beta": 0.9,
     "tags": "支付,信用卡,金融,消费金融"},
    {"name": "花旗集团", "code": "usC", "alias": "Citigroup,花旗", "beta": 1.0,
     "tags": "银行,金融,大金融,投行"},
    {"name": "强生", "code": "usJNJ", "alias": "Johnson & Johnson,强生公司", "beta": 0.6,
     "tags": "医药,医疗,制药,消费品"},
    {"name": "礼来", "code": "usLLY", "alias": "Eli Lilly,礼来公司", "beta": 1.1,
     "tags": "医药,制药,减肥药,糖尿病,创新药"},
    {"name": "联合健康", "code": "usUNH", "alias": "UnitedHealth,联合健康集团", "beta": 0.7,
     "tags": "医疗保险,医疗,健康管理,保险"},
    {"name": "辉瑞", "code": "usPFE", "alias": "Pfizer,辉瑞公司", "beta": 0.8,
     "tags": "医药,制药,疫苗,生物医药"},
    {"name": "默沙东", "code": "usMRK", "alias": "Merck,默克", "beta": 0.7,
     "tags": "医药,制药,创新药,疫苗"},
    {"name": "艾伯维", "code": "usABBV", "alias": "AbbVie,艾伯维公司", "beta": 0.8,
     "tags": "医药,制药,生物医药,免疫"},
    {"name": "雅培", "code": "usABT", "alias": "Abbott,雅培公司", "beta": 0.6,
     "tags": "医疗,诊断,医疗器械,营养"},
    {"name": "诺和诺德", "code": "usNVO", "alias": "Novo Nordisk,诺和诺德公司", "beta": 1.0,
     "tags": "医药,减肥药,糖尿病,制药"},
    {"name": "赛默飞世尔", "code": "usTMO", "alias": "Thermo Fisher,赛默飞", "beta": 0.9,
     "tags": "医疗,生命科学,诊断,仪器"},
    {"name": "施贵宝", "code": "usBMY", "alias": "Bristol Myers,百时美施贵宝", "beta": 0.8,
     "tags": "医药,制药,肿瘤,创新药"},
    {"name": "安进", "code": "usAMGN", "alias": "Amgen,安进公司", "beta": 0.7,
     "tags": "医药,生物医药,制药,创新药"},
    {"name": "吉利德科学", "code": "usGILD", "alias": "Gilead,吉利德", "beta": 0.8,
     "tags": "医药,制药,抗病毒,生物医药"},
    {"name": "莫德纳", "code": "usMRNA", "alias": "Moderna,莫德纳公司", "beta": 2.0,
     "tags": "医药,疫苗,mRNA,生物医药"},
    {"name": "可口可乐", "code": "usKO", "alias": "Coca-Cola,可乐", "beta": 0.5,
     "tags": "消费,饮料,食品,大消费"},
    {"name": "百事可乐", "code": "usPEP", "alias": "PepsiCo,百事", "beta": 0.5,
     "tags": "消费,饮料,食品,零食,大消费"},
    {"name": "宝洁", "code": "usPG", "alias": "P&G,宝洁公司", "beta": 0.5,
     "tags": "消费,日化,日用消费品,大消费"},
    {"name": "沃尔玛", "code": "usWMT", "alias": "Walmart,沃尔玛公司", "beta": 0.5,
     "tags": "零售,商超,消费,大消费"},
    {"name": "好市多", "code": "usCOST", "alias": "Costco,开市客", "beta": 0.5,
     "tags": "零售,会员店,商超,消费"},
    {"name": "麦当劳", "code": "usMCD", "alias": "McDonald's,麦当劳公司", "beta": 0.5,
     "tags": "餐饮,快餐,消费,连锁"},
    {"name": "星巴克", "code": "usSBUX", "alias": "Starbucks,星巴克公司", "beta": 0.8,
     "tags": "餐饮,咖啡,消费,连锁"},
    {"name": "耐克", "code": "usNKE", "alias": "Nike,耐克公司", "beta": 0.8,
     "tags": "消费,服装,运动,鞋服,大消费"},
    {"name": "迪士尼", "code": "usDIS", "alias": "Disney,华特迪士尼", "beta": 1.0,
     "tags": "娱乐,影视,主题公园,流媒体,IP"},
    {"name": "家得宝", "code": "usHD", "alias": "Home Depot,家得宝公司", "beta": 0.8,
     "tags": "零售,家居,建材,消费"},
    {"name": "劳氏", "code": "usLOW", "alias": "Lowe's,劳氏公司", "beta": 0.8,
     "tags": "零售,家居,建材,消费"},
    {"name": "塔吉特", "code": "usTGT", "alias": "Target,塔吉特公司", "beta": 0.8,
     "tags": "零售,商超,消费,大消费"},
    {"name": "美客多", "code": "usMELI", "alias": "MercadoLibre,拉美电商", "beta": 1.5,
     "tags": "电商,拉美,支付,金融科技"},
    {"name": "埃克森美孚", "code": "usXOM", "alias": "Exxon Mobil,美孚", "beta": 0.9,
     "tags": "能源,石油,油气,化工"},
    {"name": "雪佛龙", "code": "usCVX", "alias": "Chevron,雪佛龙公司", "beta": 0.9,
     "tags": "能源,石油,油气,化工"},
    {"name": "康菲石油", "code": "usCOP", "alias": "ConocoPhillips,康菲", "beta": 1.0,
     "tags": "能源,石油,油气,资源"},
    {"name": "卡特彼勒", "code": "usCAT", "alias": "Caterpillar,卡特彼勒公司", "beta": 1.1,
     "tags": "工业,工程机械,重工,设备"},
    {"name": "波音", "code": "usBA", "alias": "Boeing,波音公司", "beta": 1.3,
     "tags": "航空,飞机制造,国防,工业"},
    {"name": "霍尼韦尔", "code": "usHON", "alias": "Honeywell,霍尼韦尔公司", "beta": 0.8,
     "tags": "工业,航空,自动化,多元制造"},
    {"name": "GE航天航空", "code": "usGE", "alias": "General Electric,通用电气", "beta": 1.2,
     "tags": "航空,发动机,工业,能源"},
    {"name": "3M", "code": "usMMM", "alias": "3M公司", "beta": 0.9,
     "tags": "工业,化工,材料,多元制造"},
    {"name": "迪尔股份", "code": "usDE", "alias": "Deere,约翰迪尔", "beta": 0.9,
     "tags": "农业机械,工业,农机,设备"},
    {"name": "洛克希德马丁", "code": "usLMT", "alias": "Lockheed Martin,洛马", "beta": 0.7,
     "tags": "国防,军工,航空航天,军火"},
    {"name": "雷神技术", "code": "usRTX", "alias": "Raytheon,雷神", "beta": 0.9,
     "tags": "国防,军工,航空航天,发动机"},
    {"name": "联合包裹", "code": "usUPS", "alias": "UPS,联合包裹服务", "beta": 0.8,
     "tags": "物流,快递,运输,供应链"},
    {"name": "联邦快递", "code": "usFDX", "alias": "FedEx,联邦快递公司", "beta": 1.0,
     "tags": "物流,快递,运输,供应链"},
    {"name": "林德", "code": "usLIN", "alias": "Linde,林德集团", "beta": 0.7,
     "tags": "工业气体,化工,材料"},
    {"name": "美国电话电报", "code": "usT", "alias": "AT&T,美国电话电报公司", "beta": 0.6,
     "tags": "电信,通信,运营商,5G"},
    {"name": "威瑞森通信", "code": "usVZ", "alias": "Verizon,威瑞森", "beta": 0.6,
     "tags": "电信,通信,运营商,5G"},
    {"name": "T-Mobile", "code": "usTMUS", "alias": "T-Mobile美国", "beta": 0.7,
     "tags": "电信,通信,运营商,5G"},
    {"name": "康卡斯特", "code": "usCMCSA", "alias": "Comcast,康卡斯特公司", "beta": 0.7,
     "tags": "电信,媒体,有线电视,宽带"},
    {"name": "阿里巴巴", "code": "usBABA", "alias": "阿里,Alibaba", "beta": 1.5,
     "tags": "电商,云计算,人工智能,AI,中概股"},
    {"name": "拼多多", "code": "usPDD", "alias": "PDD,Temu", "beta": 1.6,
     "tags": "电商,中概股,海外电商,低价"},
    {"name": "京东", "code": "usJD", "alias": "JD,京东商城", "beta": 1.2,
     "tags": "电商,物流,中概股,零售"},
    {"name": "百度", "code": "usBIDU", "alias": "Baidu,百度公司", "beta": 1.4,
     "tags": "搜索,人工智能,AI,自动驾驶,中概股"},
    {"name": "网易", "code": "usNTES", "alias": "NetEase,网易公司", "beta": 1.2,
     "tags": "游戏,中概股,互联网,音乐"},
    {"name": "腾讯音乐", "code": "usTME", "alias": "Tencent Music,腾讯音乐娱乐", "beta": 1.3,
     "tags": "音乐,流媒体,中概股,娱乐"},
    {"name": "哔哩哔哩", "code": "usBILI", "alias": "Bilibili,B站", "beta": 1.6,
     "tags": "视频,游戏,中概股,社区,二次元"},
    {"name": "蔚来", "code": "usNIO", "alias": "NIO,蔚来汽车", "beta": 2.0,
     "tags": "新能源车,电动车,中概股,智能驾驶"},
    {"name": "小鹏", "code": "usXPEV", "alias": "Xpeng,小鹏汽车", "beta": 2.0,
     "tags": "新能源车,电动车,中概股,智能驾驶"},
    {"name": "理想汽车", "code": "usLI", "alias": "Li Auto,理想", "beta": 1.8,
     "tags": "新能源车,电动车,中概股,增程"},
    {"name": "富途控股", "code": "usFUTU", "alias": "Futu,富途", "beta": 1.8,
     "tags": "券商,中概股,金融科技,互联网券商"},
    {"name": "好未来", "code": "usTAL", "alias": "TAL Education,学而思", "beta": 1.5,
     "tags": "教育,中概股,在线教育,AI教育"},
    {"name": "新东方", "code": "usEDU", "alias": "New Oriental,新东方教育", "beta": 1.5,
     "tags": "教育,中概股,留学,直播电商"},
    {"name": "Shopify", "code": "usSHOP", "alias": "Shopify公司,电商平台", "beta": 1.8,
     "tags": "电商,SaaS,云计算,跨境电商"},
    {"name": "Spotify", "code": "usSPOT", "alias": "声田,Spotify公司", "beta": 1.4,
     "tags": "音乐,流媒体,音频,订阅"},
    {"name": "DoorDash", "code": "usDASH", "alias": "DoorDash公司,外卖", "beta": 1.6,
     "tags": "外卖,配送,餐饮,共享经济"},
    {"name": "Affirm", "code": "usAFRM", "alias": "Affirm公司,先买后付", "beta": 2.2,
     "tags": "金融科技,消费金融,支付,先买后付"},
    {"name": "Cloudflare", "code": "usNET", "alias": "科赋锐,Cloudflare公司", "beta": 1.8,
     "tags": "云计算,网络安全,CDN,边缘计算"},
    {"name": "CrowdStrike", "code": "usCRWD", "alias": "CrowdStrike公司,网络安全", "beta": 1.8,
     "tags": "网络安全,云安全,软件,AI安全"},
]



CN_POOL = [
    {"name": "贵州茅台", "code": "sh600519", "alias": "茅台", "beta": 0.9,
     "tags": "白酒,食品饮料,消费,大消费,酱酒,酒企"},
    {"name": "五粮液", "code": "sz000858", "alias": "五粮", "beta": 1.1,
     "tags": "白酒,食品饮料,消费,大消费,酒企"},
    {"name": "泸州老窖", "code": "sz000568", "alias": "泸州老窖", "beta": 1.1,
     "tags": "白酒,食品饮料,消费,大消费,酒企"},
    {"name": "山西汾酒", "code": "sh600809", "alias": "汾酒", "beta": 1.2,
     "tags": "白酒,食品饮料,消费,大消费,酒企"},
    {"name": "洋河股份", "code": "sz002304", "alias": "洋河", "beta": 1.0,
     "tags": "白酒,食品饮料,消费,大消费,酒企"},
    {"name": "海天味业", "code": "sh603288", "alias": "海天", "beta": 0.8,
     "tags": "调味品,食品,消费,大消费"},
    {"name": "伊利股份", "code": "sh600887", "alias": "伊利", "beta": 0.7,
     "tags": "乳制品,食品,消费,大消费"},
    {"name": "金龙鱼", "code": "sz300999", "alias": "金龙鱼", "beta": 0.8,
     "tags": "粮油,食品,消费,大消费"},
    {"name": "宁德时代", "code": "sz300750", "alias": "宁德,CATL", "beta": 1.6,
     "tags": "锂电池,动力电池,新能源车,电动车,储能,固态电池,电池"},
    {"name": "比亚迪", "code": "sz002594", "alias": "BYD", "beta": 1.4,
     "tags": "新能源车,电动车,汽车,整车,智驾,固态电池"},
    {"name": "隆基绿能", "code": "sh601012", "alias": "隆基", "beta": 1.5,
     "tags": "光伏,新能源,太阳能,硅片,储能"},
    {"name": "通威股份", "code": "sh600438", "alias": "通威", "beta": 1.4,
     "tags": "光伏,新能源,硅料,太阳能,农业"},
    {"name": "阳光电源", "code": "sz300274", "alias": "阳光电源", "beta": 1.6,
     "tags": "光伏,储能,逆变器,新能源"},
    {"name": "亿纬锂能", "code": "sz300014", "alias": "亿纬", "beta": 1.6,
     "tags": "锂电池,动力电池,储能,新能源"},
    {"name": "赣锋锂业", "code": "sz002460", "alias": "赣锋", "beta": 1.7,
     "tags": "锂矿,锂电池,新能源,资源,有色"},
    {"name": "招商银行", "code": "sh600036", "alias": "招行", "beta": 0.7,
     "tags": "银行,金融,大金融,信贷,存款,息差"},
    {"name": "中国平安", "code": "sh601318", "alias": "平安", "beta": 1.1,
     "tags": "保险,金融,大金融,寿险,养老"},
    {"name": "东方财富", "code": "sz300059", "alias": "东财", "beta": 1.8,
     "tags": "券商,证券,金融,大金融,两融,牛市"},
    {"name": "中信证券", "code": "sh600030", "alias": "中信证券", "beta": 1.3,
     "tags": "券商,证券,金融,投行,大金融"},
    {"name": "工商银行", "code": "sh601398", "alias": "工行", "beta": 0.5,
     "tags": "银行,金融,大金融,国有大行,高股息"},
    {"name": "建设银行", "code": "sh601939", "alias": "建行", "beta": 0.5,
     "tags": "银行,金融,大金融,国有大行,高股息"},
    {"name": "中国银行", "code": "sh601988", "alias": "中行", "beta": 0.5,
     "tags": "银行,金融,大金融,国有大行,高股息"},
    {"name": "农业银行", "code": "sh601288", "alias": "农行", "beta": 0.5,
     "tags": "银行,金融,大金融,国有大行,高股息"},
    {"name": "兴业银行", "code": "sh601166", "alias": "兴业银行", "beta": 0.7,
     "tags": "银行,金融,大金融,股份制"},
    {"name": "中国人寿", "code": "sh601628", "alias": "人寿", "beta": 1.0,
     "tags": "保险,金融,大金融,寿险"},
    {"name": "中芯国际", "code": "sh688981", "alias": "中芯", "beta": 1.7,
     "tags": "半导体,芯片,晶圆,集成电路,国产替代,光刻机"},
    {"name": "北方华创", "code": "sz002371", "alias": "北方华创", "beta": 1.8,
     "tags": "半导体,芯片,半导体设备,国产替代,光刻机"},
    {"name": "韦尔股份", "code": "sh603501", "alias": "韦尔", "beta": 1.8,
     "tags": "半导体,芯片,CIS,图像传感器,消费电子"},
    {"name": "海光信息", "code": "sh688041", "alias": "海光", "beta": 1.9,
     "tags": "半导体,CPU,国产芯片,算力,信创"},
    {"name": "寒武纪", "code": "sh688256", "alias": "寒武纪", "beta": 2.2,
     "tags": "AI芯片,人工智能,算力,国产芯片,半导体"},
    {"name": "中微公司", "code": "sh688012", "alias": "中微", "beta": 1.8,
     "tags": "半导体设备,芯片,刻蚀,国产替代"},
    {"name": "沪硅产业", "code": "sh688126", "alias": "沪硅", "beta": 1.7,
     "tags": "半导体,硅片,芯片,晶圆,材料"},
    {"name": "兆易创新", "code": "sh603986", "alias": "兆易创新", "beta": 1.8,
     "tags": "半导体,存储芯片,芯片,MCU,国产替代"},
    {"name": "浪潮信息", "code": "sz000977", "alias": "浪潮", "beta": 1.7,
     "tags": "服务器,算力,人工智能,AI,数据中心,液冷"},
    {"name": "科大讯飞", "code": "sz002230", "alias": "讯飞", "beta": 1.6,
     "tags": "人工智能,AI,大模型,算力,智能语音,机器人"},
    {"name": "金山办公", "code": "sh688111", "alias": "金山办公", "beta": 1.5,
     "tags": "软件,办公软件,AI,信创,WPS"},
    {"name": "三六零", "code": "sh601360", "alias": "360", "beta": 1.5,
     "tags": "网络安全,人工智能,AI,软件,安全"},
    {"name": "用友网络", "code": "sh600588", "alias": "用友", "beta": 1.4,
     "tags": "软件,企业软件,云计算,SaaS,信创"},
    {"name": "恒生电子", "code": "sh600570", "alias": "恒生电子", "beta": 1.5,
     "tags": "金融科技,软件,证券IT,金融,信创"},
    {"name": "紫光股份", "code": "sz000938", "alias": "紫光", "beta": 1.5,
     "tags": "服务器,云计算,网络设备,算力,信创"},
    {"name": "中科曙光", "code": "sh603019", "alias": "曙光", "beta": 1.7,
     "tags": "服务器,算力,人工智能,数据中心,信创"},
    {"name": "恒瑞医药", "code": "sh600276", "alias": "恒瑞", "beta": 1.0,
     "tags": "医药,创新药,生物医药,集采,医改,药企"},
    {"name": "迈瑞医疗", "code": "sz300760", "alias": "迈瑞", "beta": 1.0,
     "tags": "医疗器械,医药,医疗设备,创新"},
    {"name": "药明康德", "code": "sh603259", "alias": "药明", "beta": 1.5,
     "tags": "医药,CXO,创新药,研发外包,生物医药"},
    {"name": "爱尔眼科", "code": "sz300015", "alias": "爱尔", "beta": 1.3,
     "tags": "医疗服务,眼科,医药,连锁"},
    {"name": "片仔癀", "code": "sh600436", "alias": "片仔癀", "beta": 0.9,
     "tags": "中药,医药,消费,独家品种"},
    {"name": "智飞生物", "code": "sz300122", "alias": "智飞", "beta": 1.3,
     "tags": "疫苗,医药,生物医药,代理"},
    {"name": "云南白药", "code": "sz000538", "alias": "白药", "beta": 0.8,
     "tags": "中药,医药,消费,日化"},
    {"name": "复星医药", "code": "sh600196", "alias": "复星医药", "beta": 1.0,
     "tags": "医药,创新药,生物医药,投资"},
    {"name": "中航沈飞", "code": "sh600760", "alias": "沈飞", "beta": 1.3,
     "tags": "军工,国防,航空,战机,装备"},
    {"name": "航发动力", "code": "sh600893", "alias": "航发", "beta": 1.3,
     "tags": "军工,航空发动机,国防,装备"},
    {"name": "中航光电", "code": "sz002179", "alias": "中航光电", "beta": 1.2,
     "tags": "军工,连接器,国防,电子"},
    {"name": "中国船舶", "code": "sh600150", "alias": "中国船舶", "beta": 1.3,
     "tags": "军工,造船,国防,海洋工程"},
    {"name": "中直股份", "code": "sh600038", "alias": "中直", "beta": 1.3,
     "tags": "军工,直升机,航空,国防"},
    {"name": "紫金矿业", "code": "sh601899", "alias": "紫金", "beta": 1.2,
     "tags": "黄金,有色,铜,资源,大宗商品,锂矿"},
    {"name": "中国神华", "code": "sh601088", "alias": "神华", "beta": 0.7,
     "tags": "煤炭,能源,资源,高股息,央企"},
    {"name": "陕西煤业", "code": "sh601225", "alias": "陕煤", "beta": 0.8,
     "tags": "煤炭,能源,资源,高股息"},
    {"name": "洛阳钼业", "code": "sh603993", "alias": "洛钼", "beta": 1.3,
     "tags": "有色,钼,铜,钴,资源"},
    {"name": "北方稀土", "code": "sh600111", "alias": "北方稀土", "beta": 1.5,
     "tags": "稀土,有色,资源,战略资源"},
    {"name": "华友钴业", "code": "sh603799", "alias": "华友", "beta": 1.6,
     "tags": "钴,锂电材料,新能源,资源,有色"},
    {"name": "长江电力", "code": "sh600900", "alias": "长电", "beta": 0.5,
     "tags": "电力,水电,公用事业,高股息,央企"},
    {"name": "中国核电", "code": "sh601985", "alias": "中国核电", "beta": 0.7,
     "tags": "核电,电力,能源,公用事业,央企"},
    {"name": "华能国际", "code": "sh600011", "alias": "华能", "beta": 0.8,
     "tags": "电力,火电,能源,公用事业"},
    {"name": "国电南瑞", "code": "sh600406", "alias": "南瑞", "beta": 1.1,
     "tags": "电力设备,智能电网,特高压,新能源"},
    {"name": "三一重工", "code": "sh600031", "alias": "三一", "beta": 1.2,
     "tags": "工程机械,重工,设备,基建,一带一路"},
    {"name": "中联重科", "code": "sz000157", "alias": "中联", "beta": 1.1,
     "tags": "工程机械,重工,设备,基建"},
    {"name": "汇川技术", "code": "sz300124", "alias": "汇川", "beta": 1.5,
     "tags": "工业自动化,工控,机器人,电机,新能源"},
    {"name": "美的集团", "code": "sz000333", "alias": "美的", "beta": 0.9,
     "tags": "家电,消费,机器人,智能制造,大消费"},
    {"name": "格力电器", "code": "sz000651", "alias": "格力", "beta": 0.9,
     "tags": "家电,空调,消费,大消费,高股息"},
    {"name": "海尔智家", "code": "sh600690", "alias": "海尔", "beta": 0.9,
     "tags": "家电,消费,智能家居,大消费"},
    {"name": "立讯精密", "code": "sz002475", "alias": "立讯", "beta": 1.5,
     "tags": "消费电子,苹果,果链,手机,AI手机"},
    {"name": "京东方A", "code": "sz000725", "alias": "京东方", "beta": 1.3,
     "tags": "面板,显示,消费电子,半导体,OLED"},
    {"name": "海康威视", "code": "sz002415", "alias": "海康", "beta": 1.2,
     "tags": "安防,监控,人工智能,AI,物联网"},
    {"name": "大华股份", "code": "sz002236", "alias": "大华", "beta": 1.3,
     "tags": "安防,监控,人工智能,AI,物联网"},
    {"name": "歌尔股份", "code": "sz002241", "alias": "歌尔", "beta": 1.5,
     "tags": "消费电子,声学,苹果,果链,VR"},
    {"name": "中兴通讯", "code": "sz000063", "alias": "中兴", "beta": 1.3,
     "tags": "通信,5G,网络设备,算力,芯片"},
    {"name": "中国移动", "code": "sh600941", "alias": "中国移动", "beta": 0.5,
     "tags": "电信,通信,运营商,5G,央企,高股息"},
    {"name": "中国电信", "code": "sh601728", "alias": "中国电信", "beta": 0.6,
     "tags": "电信,通信,运营商,5G,云计算"},
    {"name": "分众传媒", "code": "sz002027", "alias": "分众", "beta": 1.0,
     "tags": "传媒,广告,消费,楼宇广告"},
    {"name": "芒果超媒", "code": "sz300413", "alias": "芒果", "beta": 1.3,
     "tags": "传媒,视频,综艺,内容,互联网"},
    {"name": "三七互娱", "code": "sz002555", "alias": "三七", "beta": 1.4,
     "tags": "游戏,传媒,互联网,出海"},
    {"name": "完美世界", "code": "sz002624", "alias": "完美世界", "beta": 1.4,
     "tags": "游戏,传媒,影视,互联网"},
    {"name": "万科A", "code": "sz000002", "alias": "万科", "beta": 1.0,
     "tags": "地产,房地产,物业,基建"},
    {"name": "保利发展", "code": "sh600048", "alias": "保利", "beta": 0.9,
     "tags": "地产,房地产,央企,物业"},
    {"name": "海螺水泥", "code": "sh600585", "alias": "海螺", "beta": 0.8,
     "tags": "水泥,建材,基建,周期"},
    {"name": "石头科技", "code": "sh688169", "alias": "石头", "beta": 1.5,
     "tags": "家电,扫地机器人,智能家居,消费"},
    {"name": "公牛集团", "code": "sh603195", "alias": "公牛", "beta": 0.9,
     "tags": "家电,插座,消费,智能家居"},
    {"name": "顺丰控股", "code": "sz002352", "alias": "顺丰", "beta": 1.0,
     "tags": "物流,快递,供应链,运输"},
    {"name": "中国国航", "code": "sh601111", "alias": "国航", "beta": 1.1,
     "tags": "航空,机场,运输,出行,消费"},
    {"name": "春秋航空", "code": "sh601021", "alias": "春秋", "beta": 1.2,
     "tags": "航空,低成本航空,运输,出行"},
    {"name": "福耀玻璃", "code": "sh600660", "alias": "福耀", "beta": 0.9,
     "tags": "汽车零部件,玻璃,汽车,制造"},
    {"name": "潍柴动力", "code": "sz000338", "alias": "潍柴", "beta": 1.0,
     "tags": "汽车零部件,柴油机,重卡,氢能"},
    {"name": "万华化学", "code": "sh600309", "alias": "万华", "beta": 1.0,
     "tags": "化工,新材料,MDI,周期"},
    {"name": "恒力石化", "code": "sh600346", "alias": "恒力", "beta": 1.1,
     "tags": "化工,炼化,石油化工,新材料"},
    {"name": "牧原股份", "code": "sz002714", "alias": "牧原", "beta": 1.2,
     "tags": "养殖,生猪,农业,猪周期"},
    {"name": "温氏股份", "code": "sz300498", "alias": "温氏", "beta": 1.1,
     "tags": "养殖,生猪,鸡,农业"},
    {"name": "海大集团", "code": "sz002311", "alias": "海大", "beta": 0.9,
     "tags": "饲料,农业,养殖,水产"},
    {"name": "中远海控", "code": "sh601919", "alias": "中远海控", "beta": 1.2,
     "tags": "航运,集装箱,港口,运输,周期"},
    {"name": "招商蛇口", "code": "sz001979", "alias": "招商蛇口", "beta": 1.0,
     "tags": "地产,房地产,园区,央企"},
    {"name": "中国中免", "code": "sh601888", "alias": "中免", "beta": 1.2,
     "tags": "免税,消费,旅游,零售,大消费"},
]



CN_CONSERVATIVE_CONFIG = {
    "autoTrade": False,  # 门控模式：候选股送 LLM 专家团评审后代为下单（与激进版一致）
    "scanMin": 30,       # 每 30 分钟扫一次全网快讯
    "buyPct": 20,        # 单次买入动用可用现金的比例
    "sellPct": 50,       # 单次卖出持仓的比例
    "maxPerRound": 3,    # 每轮最多成交笔数（每轮 = 一次扫描，到下一轮才重置）
    "maxPerDay": 12,     # 当日最多成交笔数（兜底，防止整天连续成交）
    "minScore": 4,       # 触发交易的最低分数（绝对值，稳健版）
    "maxPosPct": 30,     # 单只个股占总资产上限
    "cooldownMin": 30,   # 同一只股票两次交易的最小间隔
    "maxHold": 8,        # 最多同时持有几只
    "swapGate": True,    # 2026-11-13 新增：满席换仓闸门——已有「将清仓」的已批 SELL 时按先卖后买放行（用户拍板开启；/api/config 传 false 可关）
    "indexCode": "sh000001",
    "reviewCooldownMin": 60,  # 同一只股票专家评审后多久内不再进入待评审队列
    "reviewTTLHours": 24,  # 候选在待评审队列里最多等这么久，超时作废出队（防坑位被长期占死）
    "approvedTTLHours": 8,   # 2026-10-15 收紧 24→8：A股只当日成交，隔夜批准一律过期重评（防风语筑式18.6h陈旧信号）
    "sameDayBuyMax": 1,      # 同一只股票当日最多自动买入次数（防评审冷却间隙里同股连买）
    "minBuyAmount": 2000,    # 2026-10-15 新增：最小下单金额（元），低于此停买保留弹药（防零股塞单）
    "minSellAmount": 1000,   # 2026-11-10 新增：止盈分批的最小单笔金额（元），折半后低于此且整仓不大则直接清仓残股（防 313 股拆 7 笔的碎单）
    # ---- 单票超配减仓 + 卖出股数下限（2026-11-18 起，B 类参数落地）----
    "useTrim": True,           # 单票超配定期减仓总开关（传 false 可一键回退）
    "trimPct": 22,             # 触发线：连续 trimConfirmDays 个交易日权重 >22%（20~22% 是赢家漂移区，超额 +2.92%）
    "trimTargetPct": 20,       # 减仓目标权重：卖到 20%，留 2pp 缓冲防次日再触发
    "trimConfirmDays": 3,      # 连续确认交易日数（防盘中噪声）
    "trimCooldownDays": 10,    # 同标的减仓冷却交易日
    "sellFloorRatio": 2.0,     # 单笔卖出金额 ≥ 此倍数×minSellAmount（防碎单）
    "clearRemainderRatio": 1.5,  # 卖后残值 < 此倍数×minSellAmount → 一次清仓（防残股占席）
    # ---- 信号源（新闻之外的影响因素） ----
    "useTech": True,     # 技术面：均线多空/20日新高新低/量比
    "useLhb": True,      # 龙虎榜：机构游资净买净卖
    "useAnn": True,      # 公司公告：业绩预告/减持/回购/中标等
    "useVal": True,      # 估值换手过滤：亏损股、高PE、过热不追
    "sigScore": 3,       # 无新闻时，综合信号独立触发交易的门槛（2026-10-03 下调 4→3：真实信号天花板仅 +2，原门槛过严导致恒 0 成交）
    "buySigFloor": -6,   # 买入时该股信号分下限（-6=不限；0=要求信号不为负）
    # ---- 专家评议团（多角色独立研判 + 合议投票 + 风控否决） ----
    "useCouncil": True,  # 走评议团决议（关掉则退回旧的简单加分制）
    "minAgree": 2,       # 通过所需最少同意票（弃权的分析师不计入票池）
    "useRisk": True,     # 风控官一票否决权
    "maxTotalPos": 92,   # 总仓位上限%，超过则禁止再买
    "stopLossPct": -6,   # 硬止损/禁加仓线；2026-11-14 收紧 -8→-6（用户拍板+45天反事实模拟净改善+2032元）
    # ---- 动态调池：自动淘汰没前景 + 选入强势股（LLM 门控确认后生效） ----
    "useRebalance": True,        # 启用股票池自动轮动
    "rebalanceIntervalDays": 7,  # 每 N 天生成一轮调池建议
    "rebalanceMaxAdd": 6,        # 每轮最多建议选入只数
    "rebalanceMaxRemove": 6,     # 每轮最多建议淘汰只数
    "rebalanceCooldownDays": 21, # 同股被调池建议后 N 天内不重复建议
    # ---- 市场择时（借鉴 QuantsPlaybook：RSRS 阻力支撑强度 + 扩散指标 + 均线动量） ----
    "useRegime": True,           # 启用大盘择时温度计，防守档收紧买入、进攻档放宽
    "regimeBuyFloor": 2,         # 防守档把买入信号分下限上浮的幅度（buySigFloor 基础上加）
    "regimeBuyPctScale": 0.5,    # 防守档单笔买入比例缩放系数（buyPct * 此值）
    "regimePosCap": 70,          # 防守档总仓位上限%（覆盖 maxTotalPos，保留弹药）
    # ---- 波动率自适应仓位（借鉴 QuantsPlaybook：时变夏普 / 波动率目标） ----
    "useVolTarget": True,        # 高波动自动降仓、低波动可加仓（方向分之外的独立维度）
    # ---- 持仓级止损/止盈纪律（主动离场，与新闻/信号驱动卖出互补） ----
    "useStops": True,            # 启用固定止损 + 移动止盈 + 目标止盈
    "trailPct": 15,              # 移动止盈：从历史峰值回撤超过该%则卖出锁利润
    "takeProfitPct": 30,         # 目标止盈：浮盈达到该%则分批落袋（先卖一半）
    "reReviewPct": -4,           # 强制复核线；2026-11-14 前移 -5→-4（硬止损 -6 前留处置窗口）
    "reReviewGapH": 72,          # 同一持仓两次强制复核的最小间隔（小时）
    "reReviewWorsenPp": 2,       # 2026-10-30 新增；2026-11-13 收紧 3→2：莫德纳恶化 1.47pp<3pp 未获重触发，当日多亏约 70 元
    # ---- 行业轮动 RRG（相对旋转图，识别强势行业，供进攻档聚焦强势行业） ----
    "useSectorRotation": True,   # 每轮扫描算行业相对强弱，进攻档聚焦领先/转强行业
    # ---- 量价微观结构因子（买卖压力/振幅/凸显性，直接进 delta 打分） ----
    "usePriceVolume": True,      # 量价质量分折半叠加进综合信号 delta
}



CN_AGGRESSIVE_CONFIG = {
    # ===== 激进版：更低的门槛、更大的仓位、更短的冷却、更多的持仓 =====
    "strategyName": "激进版",
    "autoTrade": False,  # 门控模式：候选股全部送 LLM 专家团评审后代为下单
    "scanMin": 10,       # 每 10 分钟扫一次全网快讯
    "buyPct": 35,        # 单次买入动用可用现金的比例（稳健版 20）
    "sellPct": 60,       # 单次卖出持仓的比例（稳健版 50）
    "maxPerRound": 5,    # 每轮最多成交笔数（稳健版 3）
    "maxPerDay": 20,     # 当日最多成交笔数（稳健版 12）
    "minScore": 2,       # 触发门槛放低，机会更多（稳健版 4）
    "maxPosPct": 45,     # 单只个股占总资产上限（稳健版 30）
    "cooldownMin": 10,   # 同股两次交易最小间隔（稳健版 30）
    "maxHold": 12,       # 最多同时持有几只（稳健版 8）
    "swapGate": True,    # 2026-11-13 新增：满席换仓闸门——已有「将清仓」的已批 SELL 时按先卖后买放行（用户拍板开启；/api/config 传 false 可关）
    "indexCode": "sh000001",
    "reviewCooldownMin": 30,  # 评审冷却（稳健版 60）
    "reviewTTLHours": 24,  # 候选在待评审队列里最多等这么久，超时作废出队（防坑位被长期占死）
    "approvedTTLHours": 8,   # 2026-10-15 收紧 24→8：A股只当日成交，隔夜批准一律过期重评（防风语筑式18.6h陈旧信号）
    "sameDayBuyMax": 1,      # 同一只股票当日最多自动买入次数（防评审冷却间隙里同股连买）
    "minBuyAmount": 2000,    # 2026-10-15 新增：最小下单金额（元），低于此停买保留弹药（防零股塞单）
    "minSellAmount": 1000,   # 2026-11-10 新增：止盈分批的最小单笔金额（元），折半后低于此且整仓不大则直接清仓残股（防 313 股拆 7 笔的碎单）
    # ---- 单票超配减仓 + 卖出股数下限（2026-11-18 起，B 类参数落地）----
    "useTrim": True,           # 单票超配定期减仓总开关（传 false 可一键回退）
    "trimPct": 22,             # 触发线：连续 trimConfirmDays 个交易日权重 >22%（20~22% 是赢家漂移区，超额 +2.92%）
    "trimTargetPct": 20,       # 减仓目标权重：卖到 20%，留 2pp 缓冲防次日再触发
    "trimConfirmDays": 3,      # 连续确认交易日数（防盘中噪声）
    "trimCooldownDays": 10,    # 同标的减仓冷却交易日
    "sellFloorRatio": 2.0,     # 单笔卖出金额 ≥ 此倍数×minSellAmount（防碎单）
    "clearRemainderRatio": 1.5,  # 卖后残值 < 此倍数×minSellAmount → 一次清仓（防残股占席）
    # ---- 信号源（新闻之外的影响因素） ----
    "useTech": True,     # 技术面：均线多空/20日新高新低/量比
    "useLhb": True,      # 龙虎榜：机构游资净买净卖
    "useAnn": True,      # 公司公告：业绩预告/减持/回购/中标等
    "useVal": False,     # 估值过滤：激进版不因高 PE / 高换手而禁买
    "sigScore": 3,       # 无新闻时，综合信号独立触发交易的门槛（稳健版 4）
    "buySigFloor": -2,   # 买入信号分下限放宽（稳健版 0）
    # ---- 专家评议团（激进版：1 票即可通过） ----
    "useCouncil": True,  # 走评议团决议
    "minAgree": 1,       # 通过所需最少同意票（稳健版 2）
    "useRisk": True,     # 风控官保留否决权（激进但仍守底线）
    "maxTotalPos": 98,   # 总仓位上限%（稳健版 92）
    "stopLossPct": -8,   # 硬止损/禁加仓线；2026-11-14 收紧 -15→-8（用户拍板+45天反事实模拟净改善+4271元，-15实测只值-57）
    # ---- 动态调池：激进版换得更勤 ----
    "useRebalance": True,
    "rebalanceIntervalDays": 5,
    "rebalanceMaxAdd": 8,
    "rebalanceMaxRemove": 8,
    "rebalanceCooldownDays": 14,
    # ---- 市场择时（激进版：防守时仍保留稍高仓位，但同样收紧买入） ----
    "useRegime": True,
    "regimeBuyFloor": 1,
    "regimeBuyPctScale": 0.6,
    "regimePosCap": 80,
    # ---- 波动率自适应仓位（激进版：波动收紧更温和，但仍响应高波动） ----
    "useVolTarget": True,
    # ---- 持仓级止损/止盈纪律（激进版：止损线更宽，让利润多跑，但同样设纪律） ----
    "useStops": True,
    "trailPct": 20,              # 移动止盈回撤线放宽（激进版 20）
    "takeProfitPct": 40,         # 目标止盈位抬高（激进版 40）
    "reReviewPct": -5,           # 强制复核线；2026-11-14 前移 -8→-5（激进档保持比稳健档(-4)宽1pp，硬止损-8前留窗口）
    "reReviewGapH": 72,          # 同一持仓两次强制复核的最小间隔（小时）
    "reReviewWorsenPp": 2,       # 2026-10-30 新增；2026-11-13 收紧 3→2：莫德纳恶化 1.47pp<3pp 未获重触发，当日多亏约 70 元
    # ---- 行业轮动 RRG（激进版：更强调强势行业聚焦，加成更大） ----
    "useSectorRotation": True,
    # ---- 量价微观结构因子（激进版：同样接入 delta，但阈值更松） ----
    "usePriceVolume": True,
}



US_CONFIG = {
    "autoTrade": False,  # 门控模式：候选股送 LLM 专家团评审后代为下单（与激进版一致）
    "scanMin": 30,       # 每 30 分钟扫一次全网快讯
    "buyPct": 20,        # 单次买入动用可用现金的比例
    "sellPct": 50,       # 单次卖出持仓的比例
    "maxPerRound": 3,    # 每轮最多成交笔数（每轮 = 一次扫描，到下一轮才重置）
    "maxPerDay": 12,     # 当日最多成交笔数（兜底，防止整天连续成交）
    "minScore": 4,       # 触发交易的最低分数（绝对值）。美股提至 4，挡掉低分噪音新闻误命中
    "maxPosPct": 20,     # 2026-10-15 收紧 30→20：AMD 单票曾达 30%，科技回撤日集中度放大亏损
    "cooldownMin": 30,   # 同一只股票两次交易的最小间隔
    "maxHold": 8,        # 最多同时持有几只
    "swapGate": True,    # 2026-11-13 新增：满席换仓闸门——已有「将清仓」的已批 SELL 时按先卖后买放行（用户拍板开启；/api/config 传 false 可关）
    "indexCode": "usINX",  # 美股版：标普500 指数（替代上证 sh000001）
    "reviewCooldownMin": 60,  # 同一只股票专家评审后多久内不再进入待评审队列
    "reviewTTLHours": 24,  # 候选在待评审队列里最多等这么久，超时作废出队（防坑位被长期占死）
    "approvedTTLHours": 12,  # 2026-10-15 收紧 24→12：隔一个美东交易时段以上的批准结论过期重评
    "sameDayBuyMax": 1,      # 同一只股票当日最多自动买入次数（防评审冷却间隙里同股连买）
    "minBuyAmount": 300,     # 2026-10-15 新增：最小下单金额（美元），低于此停买保留弹药
    "minSellAmount": 300,    # 2026-11-10 新增：止盈分批的最小单笔金额（美元），同上防碎单
    # ---- 单票超配减仓 + 卖出股数下限（2026-11-18 起，B 类参数落地）----
    "useTrim": True,           # 单票超配定期减仓总开关（传 false 可一键回退）
    "trimPct": 22,             # 触发线：连续 trimConfirmDays 个交易日权重 >22%（20~22% 是赢家漂移区，超额 +2.92%）
    "trimTargetPct": 20,       # 减仓目标权重：卖到 20%，留 2pp 缓冲防次日再触发
    "trimConfirmDays": 3,      # 连续确认交易日数（防盘中噪声）
    "trimCooldownDays": 10,    # 同标的减仓冷却交易日
    "sellFloorRatio": 2.0,     # 单笔卖出金额 ≥ 此倍数×minSellAmount（防碎单）
    "clearRemainderRatio": 1.5,  # 卖后残值 < 此倍数×minSellAmount → 一次清仓（防残股占席）
    # ---- 信号源（美股版：龙虎榜/公告/估值均为 A 股专属，已关闭；仅保留技术面 + 新闻） ----
    "useTech": True,     # 技术面：均线多空/20日新高新低/量比（美股 K 线源可用）
    "useLhb": False,     # 龙虎榜：美股无此概念，关闭
    "useAnn": False,     # 公司公告：巨潮为 A 股专属，关闭
    "useVal": False,     # 估值换手过滤：A 股换手/PE 逻辑，关闭
    "sigScore": 2,       # 无新闻时，综合信号独立触发交易的门槛（2026-10-03 下调 3→2：周五收盘真实信号天花板仅 +2，原门槛过严导致恒 0 成交）
    "buySigFloor": -6,   # 买入时该股信号分下限（-6=不限；0=要求信号不为负）
    # ---- 专家评议团（多角色独立研判 + 合议投票 + 风控否决） ----
    "useCouncil": True,  # 走评议团决议（关掉则退回旧的简单加分制）
    "minAgree": 2,       # 通过所需最少同意票（弃权的分析师不计入票池）
    "useRisk": True,     # 风控官一票否决权
    "maxTotalPos": 92,   # 总仓位上限%，超过则禁止再买
    "stopLossPct": -6,   # 硬止损/禁加仓线；2026-11-14 收紧 -8→-6（用户拍板+45天反事实模拟净改善+3231元）
    # ---- 动态调池：候选强势榜为 A 股专属接口，美股暂不启用 ----
    "useRebalance": False,
    "rebalanceIntervalDays": 7,
    "rebalanceMaxAdd": 6,
    "rebalanceMaxRemove": 6,
    "rebalanceCooldownDays": 21,
    # ---- 市场择时（美股：用标普500 usINX 做 RSRS/动量，同样启用） ----
    "useRegime": True,
    "regimeBuyFloor": 2,
    "regimeBuyPctScale": 0.5,
    "regimePosCap": 70,
    # ---- 波动率自适应仓位（美股：用标普500 usINX 波动率，同样启用） ----
    "useVolTarget": True,
    # ---- 持仓级止损/止盈纪律（美股：与稳健版一致，主动离场） ----
    "useStops": True,
    "trailPct": 15,
    "takeProfitPct": 30,
    "reReviewPct": -4,           # 强制复核线；2026-11-14 前移 -5→-4（与A股稳健版一致，硬止损-6前留处置窗口）
    "reReviewGapH": 72,          # 同一持仓两次强制复核的最小间隔（小时）
    "reReviewWorsenPp": 2,       # 2026-10-30 新增；2026-11-13 收紧 3→2：莫德纳恶化 1.47pp<3pp 未获重触发，当日多亏约 70 元
    # ---- 行业轮动 RRG（美股：用标普500做基准，识别强势行业） ----
    "useSectorRotation": True,
    # ---- 量价微观结构因子（美股：同样接入 delta） ----
    "usePriceVolume": True,
}



ACCOUNTS = {
    "us": {"name": "美股", "market": "us", "pool": US_POOL, "config": US_CONFIG},
    "cn_conservative": {"name": "A股稳健", "market": "cn", "pool": CN_POOL, "config": CN_CONSERVATIVE_CONFIG},
    "cn_aggressive": {"name": "A股激进", "market": "cn", "pool": CN_POOL, "config": CN_AGGRESSIVE_CONFIG},
}

DEFAULT_ACCOUNT = "cn_conservative"

# 2026-10-15 教训：自动化用短名 cnc/cna 调接口时静默回落到默认账户，导致激进账户复盘
# 覆盖了稳健账户的存档。显式注册别名，短名映射到正确账户；未知名仍回落默认（保持兼容）。
ACCOUNT_ALIASES = {"cnc": "cn_conservative", "cna": "cn_aggressive"}
