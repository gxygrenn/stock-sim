#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模拟炒股 · 消息驱动自动交易 后端服务

只用 Python 标准库，无第三方依赖。
  GET  /api/state             读取完整状态
  POST /api/trade             手动买卖 {side, code, price, qty}
  POST /api/settle            手动结算 {pct}
  POST /api/refresh           刷新行情（指数 + 持仓真实价）
  POST /api/scan              立即执行一轮「抓新闻 -> 打分 -> 自动交易」
  POST /api/council           召集专家团对全池开会（四位分析师 + 风控官，只出意见不下单）
  POST /api/review_done       LLM 专家团写入评审结论 {code, verdict, confidence, note}
                              结论先落队列，成交由 scan() 在交易时段按最新价落地
  POST /api/inject            手动投喂一条消息 {text, score?}，走完整决策链路（演练）
  POST /api/pool              自选池增删 {action, item}
  POST /api/config            更新配置
  POST /api/reset             重置账户
  POST /api/mode              切换价格模式 {priceMode}
  POST /api/daily_review      每日复盘结论写入（复盘自动化调用，前端展示）
  POST /api/market_context    市场环境播报写入 {date, intl, sentiment}（播报自动化调用）
"""

import json
import os
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE, "data.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

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
    "indexCode": "sh000001",
    "reviewCooldownMin": 60,  # 同一只股票专家评审后多久内不再进入待评审队列
    "reviewTTLHours": 24,  # 候选在待评审队列里最多等这么久，超时作废出队（防坑位被长期占死）
    # ---- 信号源（新闻之外的影响因素） ----
    "useTech": True,     # 技术面：均线多空/20日新高新低/量比
    "useLhb": True,      # 龙虎榜：机构游资净买净卖
    "useAnn": True,      # 公司公告：业绩预告/减持/回购/中标等
    "useVal": True,      # 估值换手过滤：亏损股、高PE、过热不追
    "sigScore": 4,       # 无新闻时，综合信号独立触发交易的门槛
    "buySigFloor": -6,   # 买入时该股信号分下限（-6=不限；0=要求信号不为负）
    # ---- 专家评议团（多角色独立研判 + 合议投票 + 风控否决） ----
    "useCouncil": True,  # 走评议团决议（关掉则退回旧的简单加分制）
    "minAgree": 2,       # 通过所需最少同意票（弃权的分析师不计入票池）
    "useRisk": True,     # 风控官一票否决权
    "maxTotalPos": 92,   # 总仓位上限%，超过则禁止再买
    "stopLossPct": -8,   # 个股浮亏超过该比例，禁止加仓摊平
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
    "indexCode": "sh000001",
    "reviewCooldownMin": 30,  # 评审冷却（稳健版 60）
    "reviewTTLHours": 24,  # 候选在待评审队列里最多等这么久，超时作废出队（防坑位被长期占死）
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
    "stopLossPct": -15,  # 浮亏禁加仓线放宽（稳健版 -8）
}


US_CONFIG = {
    "autoTrade": False,  # 门控模式：候选股送 LLM 专家团评审后代为下单（与激进版一致）
    "scanMin": 30,       # 每 30 分钟扫一次全网快讯
    "buyPct": 20,        # 单次买入动用可用现金的比例
    "sellPct": 50,       # 单次卖出持仓的比例
    "maxPerRound": 3,    # 每轮最多成交笔数（每轮 = 一次扫描，到下一轮才重置）
    "maxPerDay": 12,     # 当日最多成交笔数（兜底，防止整天连续成交）
    "minScore": 2,       # 触发交易的最低分数（绝对值）
    "maxPosPct": 30,     # 单只个股占总资产上限
    "cooldownMin": 30,   # 同一只股票两次交易的最小间隔
    "maxHold": 8,        # 最多同时持有几只
    "indexCode": "usINX",  # 美股版：标普500 指数（替代上证 sh000001）
    "reviewCooldownMin": 60,  # 同一只股票专家评审后多久内不再进入待评审队列
    "reviewTTLHours": 24,  # 候选在待评审队列里最多等这么久，超时作废出队（防坑位被长期占死）
    # ---- 信号源（美股版：龙虎榜/公告/估值均为 A 股专属，已关闭；仅保留技术面 + 新闻） ----
    "useTech": True,     # 技术面：均线多空/20日新高新低/量比（美股 K 线源可用）
    "useLhb": False,     # 龙虎榜：美股无此概念，关闭
    "useAnn": False,     # 公司公告：巨潮为 A 股专属，关闭
    "useVal": False,     # 估值换手过滤：A 股换手/PE 逻辑，关闭
    "sigScore": 3,       # 无新闻时，综合信号独立触发交易的门槛（信号只剩技术面，门槛略降）
    "buySigFloor": -6,   # 买入时该股信号分下限（-6=不限；0=要求信号不为负）
    # ---- 专家评议团（多角色独立研判 + 合议投票 + 风控否决） ----
    "useCouncil": True,  # 走评议团决议（关掉则退回旧的简单加分制）
    "minAgree": 2,       # 通过所需最少同意票（弃权的分析师不计入票池）
    "useRisk": True,     # 风控官一票否决权
    "maxTotalPos": 92,   # 总仓位上限%，超过则禁止再买
    "stopLossPct": -8,   # 个股浮亏超过该比例，禁止加仓摊平
}


ACCOUNTS = {
    "us": {"name": "美股", "market": "us", "pool": US_POOL, "config": US_CONFIG},
    "cn_conservative": {"name": "A股稳健", "market": "cn", "pool": CN_POOL, "config": CN_CONSERVATIVE_CONFIG},
    "cn_aggressive": {"name": "A股激进", "market": "cn", "pool": CN_POOL, "config": CN_AGGRESSIVE_CONFIG},
}
DEFAULT_ACCOUNT = "cn_conservative"


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def default_state(account=None):
    account = account or current_account()
    acc = ACCOUNTS.get(account) or ACCOUNTS[DEFAULT_ACCOUNT]
    return {
        "account": account,
        "initCapital": 100000.0,
        "cash": 100000.0,
        "holdings": [],
        "pool": list(acc["pool"]),
        "history": [],
        "news": [],
        "trades": [],
        "log": [],
        "seenNews": [],
        "lastTrade": {},
        "roundTrades": 0,  # 本轮（本次扫描/投喂）已成交笔数，每轮开始时归零
        "idx": 100.0,
        "nextDate": today_str(),
        "priceMode": "real",
        "fees": 0.0,
        "realized": 0.0,
        "seq": 1,
        "quote": None,
        "poolQuote": {},
        "lastScan": "",
        "scanErr": "",
        "signals": {},     # code -> {tech,lhb,ann,delta,block,notes[],time}
        "sigFired": {},    # code -> "2026-09-13+" 综合信号当日已触发方向
        "council": [],     # 评议团合议记录（最近若干次）
        "pendingReview": [],  # 待 LLM 专家团评审的候选（autoTrade 关闭时由 scan 维护）
        "reviewed": {},    # code -> 最近一次专家评审时间（冷却用）
        "reviewLog": [],   # 专家团评审历史台账（code/name/verdict/confidence/note/outcome/成交价，最近 200 条，独立于队列生命周期）
        "expiredReviews": [],  # 队列 TTL 超时作废 / 已批准未成交的候选（最近 100 条）
        "dailyReviews": [],  # 每日复盘存档（最近 30 天，由复盘自动化写入）
        "marketContext": None,  # 最近一次市场环境播报（国际因素 + 市场情绪，由播报自动化写入）
        "config": dict(acc["config"]),
    }


_states = {}
_lock = threading.RLock()
_save_broken = False
_last_catchup = 0.0
_tls = threading.local()


def set_account(account):
    """设置当前线程/上下文的账户。"""
    _tls.account = account if account in ACCOUNTS else DEFAULT_ACCOUNT


def current_account():
    """当前上下文要操作的账户。"""
    return getattr(_tls, "account", DEFAULT_ACCOUNT)


def load_state(account=None):
    account = account or current_account()
    if account not in ACCOUNTS:
        account = DEFAULT_ACCOUNT
    if account in _states:
        return _states[account]
    d = default_state(account)
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            # 新格式 {account: state}；旧格式单 state 归入默认账户（向后兼容）
            acct_data = None
            if isinstance(saved, dict) and any(k in ACCOUNTS for k in saved):
                acct_data = saved.get(account)
            elif account == DEFAULT_ACCOUNT:
                acct_data = saved
            if acct_data:
                for k, v in d.items():
                    if k not in acct_data:
                        acct_data[k] = v
                for k, v in ACCOUNTS[account]["config"].items():
                    if k not in acct_data.get("config", {}):
                        acct_data["config"][k] = v
                # 老存档补 tags 字段
                for p in acct_data.get("pool", []):
                    if "tags" not in p:
                        p["tags"] = ""
                d = acct_data
        except Exception as exc:
            d["log"] = [{"time": now_str(), "msg": "读取存档失败(%s)，已使用新账户" % exc}]
    _states[account] = d
    return d


def save_state():
    """存档失败（如云端只读目录）不能拖垮服务，降级为纯内存运行。"""
    global _save_broken
    if _save_broken:
        return
    try:
        with _lock:
            if not _states:
                return
            tmp = DATA_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(_states, fh, ensure_ascii=False)
            os.replace(tmp, DATA_FILE)
    except Exception as exc:
        _save_broken = True
        print("存档写入失败，转为内存模式: %s" % exc, flush=True)


def log(msg):
    s = load_state()
    s["log"].insert(0, {"time": now_str(), "msg": msg})
    s["log"] = s["log"][:200]


# ---------------------------------------------------------------- 网络
def http_get(url, encoding="utf-8", timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode(encoding, "ignore")


def _f(p, i):
    try:
        return float(p[i]) if len(p) > i and p[i] not in ("", "-") else 0.0
    except Exception:
        return 0.0


def _fmt_ts(ts):
    """行情时间戳统一为 'YYYY-MM-DD HH:MM'。
    A股为紧凑格式 20260930160001，美股为 2026-09-30 16:00:01。"""
    if not ts:
        return ""
    if "-" in ts:
        return ts[:16]
    if len(ts) >= 12:
        return "%s-%s-%s %s:%s" % (ts[0:4], ts[4:6], ts[6:8], ts[8:10], ts[10:12])
    return ""


def fetch_quote(code):
    """指数或个股行情。腾讯 qt.gtimg.cn，GBK 编码。支持 A股(sh/sz)与美股(us)。"""
    txt = http_get("https://qt.gtimg.cn/q=" + code, encoding="gbk")
    m = re.search(r'v_\w+="([^"]*)"', txt)
    if not m or not m.group(1):
        raise RuntimeError("行情返回为空")
    p = m.group(1).split("~")
    if len(p) < 33:
        raise RuntimeError("行情字段不足")
    price = float(p[3] or 0)
    pre_close = float(p[4] or 0)
    ts = p[30] or ""
    is_us = code.startswith("us")
    # 成交额：A股 p[37] 单位为万元，美股为美元全额
    amount = _f(p, 37) * (1 if is_us else 1e4)
    return {
        "name": p[1], "code": code, "price": price, "preClose": pre_close,
        "chg": float(p[31] or 0), "pct": float(p[32] or 0),
        "amount": amount, "time": _fmt_ts(ts) or "--",
        "turn": _f(p, 38), "pe": _f(p, 39), "pb": _f(p, 46),
    }


def fetch_prices(codes):
    """批量行情，返回 {code: quote}。"""
    codes = [c for c in codes if c]
    if not codes:
        return {}
    out = {}
    for i in range(0, len(codes), 20):
        chunk = codes[i:i + 20]
        txt = http_get("https://qt.gtimg.cn/q=" + ",".join(chunk), encoding="gbk")
        for m in re.finditer(r'v_(\w+)="([^"]*)"', txt):
            code, body = m.group(1), m.group(2)
            p = body.split("~")
            if len(p) < 33:
                continue
            try:
                price, pre = float(p[3] or 0), float(p[4] or 0)
                is_us = code.startswith("us")
                amount = float(p[37]) * (1 if is_us else 1e4) if len(p) > 37 and p[37] else 0.0
            except Exception:
                continue
            out[code] = {"name": p[1], "code": code, "price": price, "preClose": pre,
                         "chg": float(p[31] or 0), "pct": float(p[32] or 0),
                         "amount": amount, "time": _fmt_ts(p[30] if len(p) > 30 else ""),
                         "turn": _f(p, 38), "pe": _f(p, 39), "pb": _f(p, 46)}
    return out


def fetch_news(limit=40):
    """新浪 7x24 财经直播。"""
    url = ("https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size=%d"
           "&zhibo_id=152&tag_id=0&dire=f&dpc=1" % limit)
    data = json.loads(http_get(url))
    lst = ((data.get("result") or {}).get("data") or {}).get("feed", {}).get("list") or []
    out = []
    for it in lst:
        text = (it.get("rich_text") or "").strip()
        if not text:
            continue
        mt = re.match(r"[【\[]([^】\]]+)[】\]]", text)
        title = mt.group(1) if mt else text[:30]
        out.append({
            "id": str(it.get("id")),
            "time": it.get("create_time") or "",
            "title": title,
            "text": text[:300],
            "tag": ",".join(t.get("name", "") for t in (it.get("tag") or [])),
        })
    return out


# ---------------------------------------------------------------- 信号源
# 公告标题关键词（强词权重 2）
ANN_GOOD = [
    "回购", "增持", "中标", "重大合同", "签署.*合同", "战略合作", "预增", "扭亏",
    "分红", "利润分配", "获批", "注册证", "投产", "量产", "股权激励", "涨价",
]
ANN_BAD = [
    "减持", "预亏", "亏损", "业绩.*下滑", "立案", "警示函", "处罚", "诉讼", "仲裁",
    "冻结", "退市", "问询函", "商誉减值", "质押", "违规", "终止.*重组", "风险提示",
]
ANN_STRONG = ["减持", "回购", "增持", "立案", "预增", "预亏", "扭亏", "中标", "重大合同", "退市"]


KLINE_HOSTS = [
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,qfq",
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,qfq",
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=%s,day,,,%d,qfq",
]
_kline_ok = 0      # 当前生效的源下标，成功即复用，避免每次都去撞被拦的主源


def fetch_kline(code, n=32):
    """腾讯日K（多主机兜底）。返回 [(date, open, close, high, low, vol), ...] 旧->新。

    主源 web.ifzq.gtimg.cn 可能被出口网络策略拦掉（返回 501），
    同一份接口在另两个主机上同样可用，逐个退避并在切换时记日志。
    """
    global _kline_ok
    order = [_kline_ok] + [i for i in range(len(KLINE_HOSTS)) if i != _kline_ok]
    last = None
    for i in order:
        try:
            d = json.loads(http_get(KLINE_HOSTS[i] % (code, n)))["data"][code]
            rows = d.get("qfqday") or d.get("day") or []
            if not rows:
                raise RuntimeError("K线为空")
            if i != _kline_ok:
                log("K线源切换 -> %s" % KLINE_HOSTS[i].split("/")[2])
                _kline_ok = i
            return [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
                    for r in rows]
        except Exception as exc:
            last = exc
    raise RuntimeError("K线全部源不可用: %s" % last)


def tech_score(s, code):
    """技术面：均线多空 / 20日新高新低 / 量比异动。返回 (分数[-3,3], 明细)。"""
    kl = fetch_kline(code, 32)
    if len(kl) < 20:
        return 0, []
    closes = [k[2] for k in kl]
    vols = [k[5] for k in kl]
    score, notes = 0, []
    ma5 = sum(closes[-5:]) / 5.0
    ma20 = sum(closes[-20:]) / 20.0
    if ma5 > ma20:
        score += 1; notes.append("均线多头+1")
    else:
        score -= 1; notes.append("均线空头-1")
    c = closes[-1]
    if c >= max(closes[-20:]):
        score += 1; notes.append("20日新高+1")
    elif c <= min(closes[-20:]):
        score -= 1; notes.append("20日新低-1")
    base = sum(vols[-6:-1]) / 5.0
    q = (s.get("poolQuote") or {}).get(code) or {}
    if base > 0 and vols[-1] > 2 * base:
        if q.get("pct", 0) >= 0:
            score += 1; notes.append("放量上涨+1")
        else:
            score -= 1; notes.append("放量下跌-1")
    return max(-3, min(3, score)), notes


def fetch_lhb():
    """东财龙虎榜（datacenter 域，与被封的 push2 不同）。返回 {6位代码: 净买额(元)}。"""
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
           "?reportName=RPT_DAILYBILLBOARD_DETAILS&columns=ALL&pageSize=300"
           "&sortColumns=TRADE_DATE&sortTypes=-1")
    d = json.loads(http_get(url))
    rows = ((d.get("result") or {}).get("data")) or []
    out = {}
    for r in rows:
        code = r.get("SECURITY_CODE") or ""
        try:
            net = float(r.get("BILLBOARD_NET_AMT") or 0)
        except Exception:
            continue
        if code:
            out[code] = out.get(code, 0) + net
    return out


def lhb_score(net):
    """龙虎榜净买额打分。返回 (分数[-2,2], 明细或空串)。"""
    yi = net / 1e8
    if net > 5e7:
        return 2, "龙虎榜净买%.2f亿+2" % yi
    if net > 1e7:
        return 1, "龙虎榜净买%.2f亿+1" % yi
    if net < -5e7:
        return -2, "龙虎榜净卖%.2f亿-2" % abs(yi)
    if net < -1e7:
        return -1, "龙虎榜净卖%.2f亿-1" % abs(yi)
    return 0, ""


def fetch_announcements(pages=3):
    """巨潮今日全市场公告，返回 [{code, title, time(ms)}]。一次拉全市场再本地过滤。"""
    se = "%s~%s" % (today_str(), today_str())
    out = []
    for pg in range(1, pages + 1):
        body = ("pageNum=%d&pageSize=100&column=sse&tabName=fulltext&plate=&stock="
                "&searchkey=&secid=&category=&trade=&seDate=%s&sortName=&sortType="
                "&isHLtitle=true" % (pg, se)).encode("utf-8")
        req = urllib.request.Request(
            "http://www.cninfo.com.cn/new/hisAnnouncement/query",
            headers={"User-Agent": UA["User-Agent"],
                     "Referer": "http://www.cninfo.com.cn/",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data=body)
        with urllib.request.urlopen(req, timeout=15) as resp:
            d = json.loads(resp.read().decode("utf-8", "ignore"))
        anns = d.get("announcements") or []
        for a in anns:
            code = str(a.get("secCode") or "")
            title = re.sub(r"<[^>]+>", "", a.get("announcementTitle") or "")
            if code and title:
                out.append({"code": code, "title": title,
                            "time": a.get("announcementTime") or 0})
        if len(anns) < 100:
            break
    return out


def ann_score(titles):
    """公告标题打分。返回 (分数[-3,3], 明细)。"""
    score, notes = 0, []
    for t in titles:
        hit_g = [w for w in ANN_GOOD if re.search(w, t)]
        hit_b = [w for w in ANN_BAD if re.search(w, t)]
        for w in hit_g:
            wgt = 2 if w in ANN_STRONG else 1
            score += wgt
            notes.append("公告[%s]%s%+d" % (w, t[:14], wgt))
        for w in hit_b:
            wgt = 2 if w in ANN_STRONG else 1
            score -= wgt
            notes.append("公告[%s]%s-%d" % (w, t[:14], wgt))
    return max(-3, min(3, score)), notes[:4]


def val_filter(s, cfg, code):
    """估值/换手过滤。返回 (是否禁买, 明细, 修正分)。"""
    if not cfg.get("useVal"):
        return False, [], 0
    q = (s.get("poolQuote") or {}).get(code) or {}
    pe, turn = q.get("pe", 0), q.get("turn", 0)
    block, notes, delta = False, [], 0
    if pe < 0:
        block = True; delta -= 1; notes.append("亏损股禁买-1")
    elif pe > 80:
        block = True; delta -= 1; notes.append("PE%.0f过高禁买-1" % pe)
    if turn > 12:
        block = True; delta -= 1; notes.append("换手%.1f%%过热禁买-1" % turn)
    return block, notes, delta


def compute_signals(s):
    """为自选池每只股票计算综合信号，写入 s['signals']。"""
    cfg = s["config"]
    lhb = {}
    if cfg.get("useLhb"):
        try:
            lhb = fetch_lhb()
        except Exception as exc:
            log("龙虎榜获取失败: %s" % exc)
    ann_by_code = {}
    if cfg.get("useAnn"):
        try:
            for a in fetch_announcements():
                ann_by_code.setdefault(a["code"], []).append(a["title"])
        except Exception as exc:
            log("公告获取失败: %s" % exc)
    signals = {}
    s["signals"] = signals          # 提前挂上：评议团预览要读同一份最新数据
    tech_fail = 0
    for item in s["pool"]:
        code = item["code"]
        num = re.sub(r"\D", "", code)
        tech, tnotes = 0, []
        if cfg.get("useTech"):
            try:
                tech, tnotes = tech_score(s, code)
            except Exception as exc:
                # 不能静默吞掉：数据源挂了必须看得见，否则信号分悄悄变 0
                tech_fail += 1
                tnotes = ["技术面数据不可用"]
                if tech_fail == 1:
                    log("技术面获取失败(%s): %s" % (code, exc))
        ls, lnote = (lhb_score(lhb[num]) if (cfg.get("useLhb") and num in lhb) else (0, ""))
        ascore, anotes = (ann_score(ann_by_code.get(num, [])) if cfg.get("useAnn") else (0, []))
        block, vnotes, vdelta = val_filter(s, cfg, code)
        delta = max(-6, min(6, tech + ls + ascore + vdelta))
        notes = tnotes + ([lnote] if lnote else []) + anotes + vnotes
        signals[code] = {"tech": tech, "lhb": ls, "ann": ascore, "delta": delta,
                         "block": block, "notes": notes[:6], "time": now_str()}
        # 评议团预览：四位分析师此刻对该股的倾向（不记录、不否决）
        try:
            prev = run_council(s, item, code, news_score=0, skip_risk=True, record=False,
                               advisory=True)
            signals[code]["council"] = {"score": prev["score"], "agree": prev["agree"],
                                        "total": prev["total"], "verdict": prev["verdict"],
                                        "votes": prev["votes"][:4]}
        except Exception:
            pass
    if tech_fail:
        log("技术面数据获取失败 %d/%d 只，本轮该维度记 0 分" % (tech_fail, len(s["pool"])))
    s["signals"] = signals
    return signals



def score_text(text):
    """返回 (分数, 利好命中, 利空命中)。标题权重 x2。"""
    mt = re.match(r"[【\[]([^】\]]+)[】\]]", text)
    head = mt.group(1) if mt else ""
    body = text
    good, bad = [], []
    for w in GOOD:
        n = (2 if w in head else 0) + body.count(w)
        if n:
            good.append("%s×%d" % (w, n))
    for w in BAD:
        n = (2 if w in head else 0) + body.count(w)
        if n:
            bad.append("%s×%d" % (w, n))
    score = sum(int(x.split("×")[1]) for x in good) - sum(int(x.split("×")[1]) for x in bad)
    return score, good, bad


def match_pool(text, pool, title=None):
    """返回 (命中的股票名列表, 命中关键词列表)。

    名称/简称/代码在**全文**里匹配——股票自己的名字出现在哪都是强信号；
    板块主题词只在**标题**里认。否则像「金融」这种常见词，在一篇毫不相关
    的稿件正文里被顺带提一句就会误命中（实测：讲加拿大加入欧盟的稿子因为
    写了「一批来自金融与政界的非正式顾问」，一次命中招行+平安+东财）。
    """
    hits, keys = [], []
    head = text if title is None else title
    for item in pool:
        name = item.get("name", "")
        num = re.sub(r"\D", "", item.get("code", ""))
        strong = [name] + [a for a in (item.get("alias") or "").split(",") if a]
        if num:
            strong.append(num)
        tags = [t for t in (item.get("tags") or "").split(",") if t]
        for k in strong:
            if k and k in text:
                hits.append(name)
                keys.append(k)
                break
        else:
            for k in tags:
                if k and k in head:
                    hits.append(name)
                    keys.append(k)
                    break
    return hits, keys


def pool_keys(item):
    """某只股票的全部匹配词，用于前端展示。"""
    ks = [item.get("name", "")]
    ks += [a for a in (item.get("alias") or "").split(",") if a]
    return [k for k in ks if k]


# ---------------------------------------------------------------- 账户
def total_mv(s):
    return sum(h["price"] * h["shares"] for h in s["holdings"])


def total_asset(s):
    return s["cash"] + total_mv(s)


def buy_fee(amount):
    # 美股零佣金（对标 Robinhood 等零佣金通道），无印花税/过户费
    return 0.0


def sell_fee(amount):
    # 仅 SEC 交易费（约 22.9 美元/百万，仅卖出端收取）
    return round(amount * 0.000023, 2)


def find_holding(s, code):
    for h in s["holdings"]:
        if h["code"] == code:
            return h
    return None


def do_buy(s, name, code, price, qty, reason):
    qty = int(qty)     # 美股 1 股起，不整手
    if qty <= 0 or price <= 0:
        return None
    amount = price * qty
    fee = buy_fee(amount)
    if amount + fee > s["cash"]:
        return None
    s["cash"] -= amount + fee
    s["fees"] += fee
    h = find_holding(s, code)
    if h is None:
        h = {"id": s["seq"], "name": name, "code": code, "price": price,
             "preClose": price, "shares": 0, "cost": 0.0, "beta": 1.0, "pct": 0.0}
        s["seq"] += 1
        s["holdings"].append(h)
    h["cost"] = (h["cost"] * h["shares"] + amount) / (h["shares"] + qty)
    h["shares"] += qty
    h["price"] = price
    tr = {"time": now_str(), "side": "买入", "name": name, "code": code,
          "price": round(price, 3), "qty": qty, "amount": round(amount, 2), "reason": reason}
    s["trades"].insert(0, tr)
    s["trades"] = s["trades"][:300]
    s["lastTrade"][code] = now_str()
    s["roundTrades"] = int(s.get("roundTrades", 0)) + 1
    log("买入 %s %d 股 @%.3f（%s）" % (name, qty, price, reason))
    return tr


def do_sell(s, name, code, price, qty, reason):
    h = find_holding(s, code)
    if h is None or h["shares"] <= 0:
        return None
    qty = min(int(qty), h["shares"])
    if qty <= 0:
        return None
    amount = price * qty
    fee = sell_fee(amount)
    s["cash"] += amount - fee
    s["fees"] += fee
    s["realized"] += (price - h["cost"]) * qty
    h["shares"] -= qty
    if h["shares"] <= 0:
        h["shares"] = 0
        h["cost"] = 0.0
    h["price"] = price
    tr = {"time": now_str(), "side": "卖出", "name": name, "code": code,
          "price": round(price, 3), "qty": qty, "amount": round(amount, 2), "reason": reason}
    s["trades"].insert(0, tr)
    s["trades"] = s["trades"][:300]
    s["lastTrade"][code] = now_str()
    s["roundTrades"] = int(s.get("roundTrades", 0)) + 1
    log("卖出 %s %d 股 @%.3f（%s）" % (name, qty, price, reason))
    return tr


def next_trading_day(ds):
    y, m, d = [int(x) for x in ds.split("-")]
    cur = datetime(y, m, d) + timedelta(days=1)
    while cur.weekday() >= 5:
        cur += timedelta(days=1)
    return cur.strftime("%Y-%m-%d")


def settle(s, mkt_pct, date_str=None):
    """结算：记录当日快照。当日盈亏 = 总资产变化（含手续费）。"""
    prev = s["history"][-1]["total"] if s["history"] else s["initCapital"]
    total = total_asset(s)
    s["idx"] = s["idx"] * (1 + mkt_pct / 100.0)
    s["history"].append({
        "date": date_str or s["nextDate"],
        "mkt": round(mkt_pct, 4),
        "total": round(total, 2),
        "dayPnl": round(total - prev, 2),
        "ret": round((total / s["initCapital"] - 1) * 100, 4),
        "mret": round(s["idx"] - 100, 4),
    })
    s["nextDate"] = next_trading_day(date_str or s["nextDate"])


# ---------------------------------------------------------------- 扫描
def refresh_prices(s):
    """刷新大盘指数；真实价格模式下刷新持仓现价。返回错误信息或空串。"""
    err = ""
    cfg = s["config"]
    try:
        s["quote"] = fetch_quote(cfg.get("indexCode", "sh000001"))
    except Exception as exc:
        err = "指数行情失败: %s" % exc
    if s["priceMode"] == "real" and s["holdings"]:
        try:
            quotes = fetch_prices([h["code"] for h in s["holdings"]])
            for h in s["holdings"]:
                q = quotes.get(h["code"])
                if q and q["price"] > 0:
                    h["preClose"] = q["preClose"] or h["preClose"]
                    h["price"] = q["price"]
                    h["pct"] = q["pct"]
        except Exception as exc:
            err = (err + " / " if err else "") + "个股行情失败: %s" % exc
    if s["pool"]:
        try:
            s["poolQuote"] = fetch_prices([p["code"] for p in s["pool"]])
        except Exception:
            pass
    return err


# ---------------------------------------------- 专家评议团
# 四位分析师各自独立出意见（不看别人），加权合议投票；风控官持一票否决权。
ANALYSTS = [
    ("技术分析师", 1.0),
    ("资金面分析师", 1.2),
    ("消息面分析师", 1.0),
    ("估值分析师", 0.8),
]


def _stance(x):
    return "看多" if x > 0 else ("看空" if x < 0 else "中性")


def _val_opinion(s, code):
    """估值分析师的独立意见：低估值给买入倾向，高估值/亏损给卖出倾向。"""
    q = (s.get("poolQuote") or {}).get(code) or {}
    try:
        pe = float(q.get("pe") or 0)
    except Exception:
        pe = 0.0
    if pe < 0:
        return -2, "亏损股，PE为负"
    if pe > 80:
        return -1, "PE%.0f 明显偏贵" % pe
    if pe > 45:
        return -1, "PE%.0f 估值偏高" % pe
    if 0 < pe <= 30:
        return 1, "PE%.0f 估值合理" % pe
    return 0, "PE%.0f 中性" % pe


def _flow_opinion(s, code, sig, q):
    """资金面分析师：龙虎榜净买卖为主；无席位时看成交额是否放量，且必须与涨跌同向。"""
    l = int(sig.get("lhb", 0) or 0)
    if l > 0:
        return l, "龙虎榜净买入"
    if l < 0:
        return l, "龙虎榜净卖出"
    try:
        amt = float(q.get("amount") or 0)
    except Exception:
        amt = 0.0
    try:
        p = float(q.get("pct") or 0)
    except Exception:
        p = 0.0
    if amt >= 80e8:
        if p >= 0:
            return 1, "巨额成交%.0f亿上涨" % (amt / 1e8)
        return -1, "巨额成交%.0f亿下跌" % (amt / 1e8)
    if 0 < amt < 3e8:
        return -1, "成交清淡仅%.1f亿" % (amt / 1e8)
    return 0, "成交%.0f亿，无席位" % (amt / 1e8)


def _risk_check(s, item, code, direction, price, advisory=False):
    """风控官审查。返回否决理由，放行返回空串。

    advisory=True 表示这是人工召集的「研判会」（只出意见、不下单）：
    跳过「本轮笔数上限 / 冷却期」这类**执行额度**约束——那是回答「现在能不能
    下单」，不是「这笔交易该不该做」。实质性风险项（估值过滤、仓位上限、
    浮亏禁加仓、现金不足、未持仓）在研判模式下依然照拦。
    """
    cfg = s["config"]
    sig = (s.get("signals") or {}).get(code) or {}
    if not direction:
        return ""
    h = find_holding(s, code)
    # 执行额度约束：只在真要下单时生效
    if not advisory:
        stop = quota_block(s)
        if stop:
            return stop
        last = s["lastTrade"].get(code)
        if last:
            try:
                cool = (datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds()
                if cool < cfg["cooldownMin"] * 60:
                    return "距上次交易不足%d分钟" % cfg["cooldownMin"]
            except Exception:
                pass
    if direction == "买入":
        if price <= 0:
            return "无有效报价"
        if sig.get("block"):
            return "估值过滤未通过（高PE/亏损/换手过热）"
        if h and h["shares"] > 0 and h["cost"] > 0:
            pnl = (price - h["cost"]) / h["cost"] * 100.0
            if pnl <= cfg.get("stopLossPct", -8):
                return "该股浮亏%.1f%%，禁止加仓摊平" % pnl
        alive = [x for x in s["holdings"] if x["shares"] > 0]
        if len(alive) >= cfg["maxHold"] and (h is None or h["shares"] <= 0):
            return "持仓只数已达上限%d" % cfg["maxHold"]
        total = total_asset(s) or 1.0
        if h and h["shares"] > 0 and (h["price"] * h["shares"]) / total * 100.0 >= cfg["maxPosPct"]:
            return "单票仓位已达%.0f%%上限" % cfg["maxPosPct"]
        mv = sum(x["price"] * x["shares"] for x in s["holdings"])
        if mv / total * 100.0 >= cfg.get("maxTotalPos", 92):
            return "总仓位超%.0f%%，保留弹药" % cfg.get("maxTotalPos", 92)
        if s["cash"] < price * 1:
            return "现金不足一手"
    else:
        if not h or h["shares"] <= 0:
            return "未持仓，无可卖"
    return ""


def run_council(s, item, code, news_score=0, news_title="", skip_risk=False, record=True,
                session="", advisory=False):
    """开一次专家会：四位分析师独立研判 -> 加权合议 -> 风控官否决。

    advisory=True 时按「研判会」处理：只出意见不下单，风控官不查执行额度。
    返回 {verdict(买入/卖出/观望/否决), score, agree, total, veto, votes[], time}。
    """
    cfg = s["config"]
    sig = (s.get("signals") or {}).get(code) or {}
    q = (s.get("poolQuote") or {}).get(code) or {}
    price = current_price(s, item) or 0

    t = int(sig.get("tech", 0) or 0) if cfg.get("useTech") else 0
    tnotes = [x for x in (sig.get("notes") or []) if x.startswith(("均线", "20日", "放量"))][:3]
    tnote = " ".join(tnotes)[:28] or "形态平淡"
    tech_op = (t, tnote)

    flow_op = _flow_opinion(s, code, sig, q) if cfg.get("useLhb") else (0, "资金面未启用")
    ns = max(-3, min(3, int(news_score or 0)))
    news_note = ("利好快讯 %+d" % news_score) if news_score > 0 else \
                (("利空快讯 %+d" % news_score) if news_score < 0 else "无相关快讯")
    news_op = (ns, news_note)
    val_op = _val_opinion(s, code) if cfg.get("useVal") else (0, "估值未启用")

    votes, wsum, wscore = [], 0.0, 0.0
    for (role, w), (sc, note) in zip(ANALYSTS, [tech_op, flow_op, news_op, val_op]):
        sc = max(-3, min(3, int(sc)))
        votes.append({"role": role, "stance": _stance(sc), "score": sc, "note": note[:30]})
        wsum += w
        wscore += sc * w
    wscore = (wscore / wsum) if wsum else 0.0

    if wscore > 0.2:
        direction = "买入"
    elif wscore < -0.2:
        direction = "卖出"
    else:
        direction = ""
    # ---- 合议表决：弃权者不计入票池，同意票需达门槛且严格多于反对票 ----
    voters = [v for v in votes if v["stance"] != "中性"]
    need = int(cfg.get("minAgree", 2))
    n_agree = n_oppose = 0
    if direction:
        want = "看多" if direction == "买入" else "看空"
        hate = "看空" if direction == "买入" else "看多"
        n_agree = sum(1 for v in voters if v["stance"] == want)
        n_oppose = sum(1 for v in voters if v["stance"] == hate)

    # 议案落地性：卖出但并未持仓 -> 议案自然失效（属于「无需表决」，不算风控否决）
    if direction == "卖出":
        h0 = find_holding(s, code)
        if not h0 or h0["shares"] <= 0:
            direction = ""
            n_agree = n_oppose = 0

    risk_on = bool(cfg.get("useRisk", True)) and not skip_risk
    veto = _risk_check(s, item, code, direction, price, advisory=advisory) if risk_on else ""
    if not direction:
        verdict = "观望"
    elif veto:
        verdict = "否决"
    elif n_agree >= need and n_agree > n_oppose:
        verdict = direction
    else:
        verdict = "观望"

    if not cfg.get("useRisk", True):
        risk_note = "风控官已关闭"
    elif skip_risk:
        risk_note = "预览未审查"
    elif veto:
        risk_note = veto
    else:
        risk_note = "无需表决" if not direction else ("研判放行" if advisory else "风控指标正常")
    votes.append({"role": "风控官", "stance": ("否决" if veto else "放行"), "score": 0,
                  "note": risk_note[:30]})
    rec = {
        "time": now_str(), "code": code, "name": item.get("name", ""),
        "verdict": verdict, "score": round(wscore, 2),
        "agree": n_agree, "oppose": n_oppose, "total": len(voters),
        "abstain": len(ANALYSTS) - len(voters), "proposal": direction,
        "veto": veto, "news": (news_title or "")[:26], "votes": votes,
        "session": session, "advisory": bool(advisory),
    }
    if record:
        s["council"] = ([rec] + list(s.get("council") or []))[:24]
    return rec


def decide(s, n, force_trade=False, min_score=None):
    """对**一条**消息走完整决策：打分 -> 命中 -> 阈值 -> 下单。

    抽出来是为了让「自动扫描」与「手动投喂演练」走完全同一条链路，
    避免两边逻辑漂移。返回执行结果字符串（写入 n["action"]）。
    """
    cfg = s["config"]
    score, good, bad = score_text(n["text"])
    if n.get("forceScore") is not None:      # 演练用：显式指定分值
        score = int(n["forceScore"])
    hits, hitkeys = match_pool(n["text"], s["pool"], n.get("title"))
    n["score"] = score
    n["good"] = good[:6]
    n["bad"] = bad[:6]
    n["hits"] = hits
    n["hitKeys"] = hitkeys[:6]
    gate = cfg["minScore"] if min_score is None else min_score
    if not hits or abs(score) < gate:
        n["action"] = "忽略"
    elif not (cfg["autoTrade"] or force_trade):
        n["action"] = "待执行"
    else:
        n["action"] = execute(s, n, hits, score)
    return n["action"]


def daily_facts(s):
    """当日事实包：供复盘官判断决策对错，避免其自行拼数据出错。"""
    today = datetime.now().strftime("%Y-%m-%d")
    q = s.get("poolQuote") or {}

    def cur_of(code):
        return (q.get(code) or {}).get("price") or 0

    def enrich(t):
        row = {k: t.get(k) for k in ("time", "side", "name", "code", "price", "qty", "amount", "reason")}
        cur = cur_of(t.get("code") or "")
        if cur and t.get("price"):
            row["last"] = round(cur, 3)
            row["chgPctSinceTrade"] = round((cur / t["price"] - 1) * 100, 2)
            sign = 1 if t.get("side") == "买入" else -1
            row["pnlIfHeld"] = round((cur - t["price"]) * t["qty"] * sign, 2)
        else:
            row["last"] = None
            row["chgPctSinceTrade"] = None
            row["pnlIfHeld"] = None
        return row

    trades = [enrich(t) for t in (s.get("trades") or [])
              if (t.get("time") or "").startswith(today)]
    holdings = [{"name": h["name"], "code": h["code"], "shares": h["shares"],
                 "cost": round(h.get("cost") or 0, 3), "price": h.get("price"),
                 "pnlPct": round((h["price"] / h["cost"] - 1) * 100, 2) if h.get("cost") else 0}
                for h in s["holdings"] if h["shares"] > 0]
    news = [n for n in (s.get("news") or []) if (n.get("time") or "").startswith(today)]
    hit_news = [{"time": n.get("time"), "title": n.get("title"), "score": n.get("score"),
                 "hits": n.get("hits"), "action": n.get("action")}
                for n in news if n.get("hits")]
    hit_news.sort(key=lambda x: -abs(x.get("score") or 0))
    idx = s.get("quote") or {}
    total = total_asset(s)
    hist = s.get("history") or []
    today_hist = [h for h in hist if (h.get("date") or "") == today]
    reviewed_today = sorted([{"code": k, "time": v}
                             for k, v in (s.get("reviewed") or {}).items()
                             if (v or "").startswith(today)], key=lambda x: x["time"])
    council_today = [{"time": r.get("time"), "name": r.get("name"), "verdict": r.get("verdict"),
                      "agree": r.get("agree"), "oppose": r.get("oppose"), "veto": r.get("veto"),
                      "proposal": r.get("proposal"), "news": r.get("news")}
                     for r in (s.get("council") or [])
                     if (r.get("time") or "").startswith(today)]
    return {
        "date": today,
        "priceMode": s.get("priceMode"),
        "account": {
            "cash": round(s["cash"], 2), "totalAsset": round(total, 2),
            "initCapital": s["initCapital"],
            "pnlTotal": round(total - s["initCapital"], 2),
            "retTotalPct": round((total / s["initCapital"] - 1) * 100, 2),
            "todayPnl": today_hist[-1].get("dayPnl") if today_hist else None,
            "realized": round(s.get("realized") or 0, 2),
            "fees": round(s.get("fees") or 0, 2),
        },
        "market": {"code": s["config"].get("indexCode"), "name": idx.get("name"),
                   "price": idx.get("price"), "pct": idx.get("pct")},
        "trades": trades,
        "holdings": holdings,
        "newsStats": {"total": len(news), "hitCount": len(hit_news), "topHits": hit_news[:10]},
        "pendingReview": [dict(p, waitH=round(age_hours(p.get("queuedAt") or p.get("time")), 1))
                          for p in (s.get("pendingReview") or [])],
        "expiredRecent": (s.get("expiredReviews") or [])[-5:],
        "reviewedToday": reviewed_today,
        "reviewLog": (s.get("reviewLog") or [])[-200:],
        "councilToday": council_today[:10],
        "marketContext": s.get("marketContext"),
        "historyLast10": hist[-10:],
        "lastScan": s.get("lastScan"),
        "scanErr": s.get("scanErr"),
    }


def age_hours(ts):
    """时间戳距今多少小时；无法解析返回 -1。"""
    try:
        return (datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600.0
    except Exception:
        return -1.0


def us_dst(now=None):
    """美国夏令时（DST）判断：3月第二个周日 02:00 开始，11月第一个周日 02:00 结束。"""
    d = now or datetime.now()
    # 3月第二个周日
    mar1 = datetime(d.year, 3, 1)
    second_sun_mar = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    # 11月第一个周日
    nov1 = datetime(d.year, 11, 1)
    first_sun_nov = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    return second_sun_mar <= d < first_sun_nov


def cn_trading(now):
    """A 股连续竞价时段（工作日 09:30-11:30 / 13:00-15:00）。"""
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return (570 <= hm <= 690) or (780 <= hm <= 900)


def us_trading(now):
    """美股常规交易时段（按北京时间换算）。夏令时 21:30-次日04:00 / 冬令时 22:30-次日05:00。"""
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    if us_dst(now):
        return (hm >= 21 * 60 + 30) or (hm < 4 * 60)      # 21:30 - 次日 04:00
    return (hm >= 22 * 60 + 30) or (hm < 5 * 60)          # 22:30 - 次日 05:00


def in_trading_hours(now=None):
    """按当前账户判断是否处于交易时段。

    评审与执行解耦后由它决定「已批准的结论现在能不能落地」：
    盘中直接按最新价成交；盘后评审出的结论挂到下一个盘中时点再执行。
    """
    now = now or datetime.now()
    if (ACCOUNTS.get(current_account()) or {}).get("market") == "us":
        return us_trading(now)
    return cn_trading(now)


def cached_price(s, code):
    """取上一次刷新缓存下来的现价（不发网络请求），用于入队时留档信号价。"""
    q = (s.get("poolQuote") or {}).get(code) or {}
    try:
        return float(q.get("price") or 0)
    except Exception:
        return 0.0


def expire_stale_reviews(s):
    """队列 TTL：等太久的候选作废出队，释放坑位，避免队列被永久占死。

    - 未评审（waiting）：从入队时间 queuedAt 起算，超 TTL 作废
    - 已批准（approved）：**不因 TTL 作废**（已过专家团评审，是有效决策），
      由 execute_approved 在交易时段成交，或用业务原因（信号跌破下限/资金不足/持仓满）drop
    - 旧格式条目（没有 queuedAt 的）一律视为已过期
    """
    cfg = s["config"]
    try:
        ttl = float(cfg.get("reviewTTLHours", 24) or 24)
    except Exception:
        ttl = 24.0
    pend = s.get("pendingReview") or []
    if not pend:
        return 0
    keep, dropped = [], []
    for p in pend:
        if (p.get("state") or "waiting") == "approved":
            keep.append(p)     # approved 已过专家团评审，不因墙钟 TTL 作废；交给 execute_approved 成交或业务原因 drop
            continue
        base = p.get("queuedAt") or p.get("time")
        aged = age_hours(base)
        if aged < 0 or aged > ttl:
            dropped.append((p, aged if aged >= 0 else ttl))
        else:
            keep.append(p)
    if not dropped:
        return 0
    s["pendingReview"] = keep
    reviewed = s.setdefault("reviewed", {})
    exp = s.setdefault("expiredReviews", [])
    for p, aged in dropped:
        exp.append({"code": p.get("code"), "name": p.get("name"),
                    "direction": p.get("direction"), "state": p.get("state") or "waiting",
                    "waitedH": round(aged, 1), "why": "TTL 超时未评审", "time": now_str()})
        reviewed[(p.get("code") or "").lower()] = now_str()   # 记冷却（key 统一小写），避免同一条信号立刻又被塞回来
    s["expiredReviews"] = exp[-100:]
    log("评审队列 TTL 作废 %d 只：%s"
        % (len(dropped), "、".join((p.get("name") or p.get("code")) for p, _ in dropped)))
    return len(dropped)


def _link_review_log(s, code, price, action):
    """成交后把成交价/动作回写到评审台账对应条目，实现「评审→成交→结果」可追溯。"""
    rl = s.get("reviewLog") or []
    for r in reversed(rl):
        if (r.get("code") or "").lower() == (code or "").lower() and r.get("fillPrice") is None:
            r["fillPrice"] = price
            r["fillTime"] = now_str()
            r["outcome"] = action
            return


def execute_approved(s):
    """把 LLM 专家团「已批准」的候选落地成成交（评审与执行解耦的核心）。

    只在交易时段内执行，一律按**当前最新价**成交，并把「评审 -> 成交」的滞后
    写进日志，让复盘能区分「结论对」与「执行晚」。非交易时段直接返回；
    无报价、持仓已满等可恢复情况留在队列里下轮再试。
    """
    pend = s.get("pendingReview") or []
    if not pend or not in_trading_hours():
        return 0
    cfg = s["config"]
    by_code = {p["code"]: p for p in s["pool"]}

    def drop(p, why):
        exp = s.setdefault("expiredReviews", [])
        exp.append({"code": p.get("code"), "name": p.get("name"),
                    "direction": p.get("direction"), "state": "rejected",
                    "why": why, "time": now_str()})
        s["expiredReviews"] = exp[-100:]
        pend.remove(p)
        log("已批准候选 %s 未成交出队：%s" % (p.get("name") or p.get("code"), why))

    done = 0
    for p in list(pend):
        if (p.get("state") or "") != "approved":
            continue
        stop = quota_block(s)
        if stop:
            log("评审补做中止：%s" % stop)
            break
        item = by_code.get(p["code"])
        if item is None:
            drop(p, "该股已不在自选池")
            continue
        rev = p.get("review") or {}
        verdict = (rev.get("verdict") or "").upper()
        conf = rev.get("confidence") or 0
        sig = (s.get("signals") or {}).get(p["code"]) or {}
        price = current_price(s, item)
        if price <= 0:
            continue                      # 没报价，留着下轮再试
        lag = age_hours(p.get("reviewedAt") or p.get("time"))
        lagtxt = ("评审后 %.1fh 按最新价" % lag) if lag >= 0 else "按最新价"
        reason = "专家团%s信心%s(%s):%s" % (verdict, conf or "-", lagtxt,
                                            (p.get("reason") or "")[:16])
        if verdict == "BUY":
            if sig.get("block"):
                drop(p, "已被估值过滤")
                continue
            floor = cfg.get("buySigFloor", -6)
            sd = int(sig.get("delta", 0) or 0)
            if sd < floor:
                drop(p, "信号分 %+d 跌破下限 %+d" % (sd, floor))
                continue
            if len([h for h in s["holdings"] if h["shares"] > 0]) >= cfg["maxHold"] \
                    and find_holding(s, p["code"]) is None:
                continue                  # 持仓已满，留着等卖出一只再执行
            total = total_asset(s)
            room = total * cfg["maxPosPct"] / 100.0
            h = find_holding(s, p["code"])
            if h:
                room -= h["price"] * h["shares"]
            budget = min(s["cash"] * cfg["buyPct"] / 100.0, room, s["cash"] * 0.98)
            qty = int(budget / price)
            if qty < 1:
                drop(p, "资金或单票额度不足")
                continue
            tr = do_buy(s, item["name"], p["code"], price, qty, reason)
            if tr:
                pend.remove(p)
                done += 1
                _link_review_log(s, p["code"], price, "已买入")
                log("评审补做买入 %s %d 股 @%.3f（%s）" % (item["name"], qty, price, lagtxt))
        elif verdict == "SELL":
            h = find_holding(s, p["code"])
            if not h or h["shares"] <= 0:
                drop(p, "已无持仓")
                continue
            qty = max(100, int(h["shares"] * cfg["sellPct"] / 100.0 // 100) * 100)
            qty = min(qty, h["shares"])
            tr = do_sell(s, item["name"], p["code"], price, qty, reason)
            if tr:
                pend.remove(p)
                done += 1
                _link_review_log(s, p["code"], price, "已卖出")
                log("评审补做卖出 %s %d 股 @%.3f（%s）" % (item["name"], qty, price, lagtxt))
    return done


def maybe_catchup(s):
    """来访触发补成交：交易时段内若队列里有「已批准」结论，按最新价落地。

    成交原本只靠 worker 后台线程在交易时段存活；云端沙箱无人访问时 worker 挂起，
    导致 approved 结论错过整个交易时段（09-28 已实锤 trades=0）。这里让任何一次
    面板刷新/自动化访问都成为补成交入口，摆脱对后台线程的依赖。
    """
    global _last_catchup
    if not in_trading_hours():
        return 0
    pend = s.get("pendingReview") or []
    if not any((p.get("state") or "") == "approved" for p in pend):
        return 0
    if time.time() - _last_catchup < 45.0:   # 节流：45 秒内最多试一次，避免每请求都拉行情
        return 0
    _last_catchup = time.time()
    done = execute_approved(s)
    if done:
        save_state()
    return done


def scan(s, force_trade=False):
    """算信号 -> 抓新闻 -> 打分 -> 匹配个股 -> 自动交易；无新闻的强信号也独立触发。"""
    cfg = s["config"]
    s["roundTrades"] = 0           # 新的一轮，笔数额度重新计
    err = refresh_prices(s)
    s["lastScan"] = now_str()

    # 先算全池综合信号（技术面/龙虎榜/公告/估值），新闻决策要用
    try:
        compute_signals(s)
    except Exception as exc:
        log("信号计算异常: %s" % exc)

    try:
        news = fetch_news(40)
    except Exception as exc:
        s["scanErr"] = "新闻抓取失败: %s" % exc
        log(s["scanErr"])
        save_state()
        return {"error": s["scanErr"]}

    seen = set(s["seenNews"])
    before = len(s["trades"])      # 一条消息可能命中多只股票，用流水增量统计成交笔数
    fresh = []
    for n in news:
        if n["id"] in seen:
            continue
        seen.add(n["id"])
        decide(s, n, force_trade=force_trade)
        fresh.append(n)
        if len(fresh) >= 25:
            break

    # 无新闻独立触发：综合信号够强（每日每方向每只股票只触发一次）
    if cfg["autoTrade"] or force_trade:
        fired = s.setdefault("sigFired", {})
        today = today_str()
        for item in s["pool"]:
            code = item["code"]
            sig = (s.get("signals") or {}).get(code) or {}
            d0 = sig.get("delta", 0)
            if abs(d0) < cfg.get("sigScore", 4):
                continue
            mark = today + ("+" if d0 > 0 else "-")
            if fired.get(code) == mark:
                continue
            notes = " ".join(sig.get("notes", [])[:2])
            act = execute(s, {"title": ("综合信号 " + notes)[:22]}, [item["name"]],
                          d0, apply_delta=False)
            if act.startswith(("买入", "卖出")):
                fired[code] = mark
                log("综合信号触发 %s(%+d) → %s" % (item["name"], d0, act))

    # ---- 评审与执行解耦：先作废等太久的候选，再把已批准的结论在盘内按最新价落地 ----
    expire_stale_reviews(s)
    approved_done = execute_approved(s)
    if approved_done:
        log("本轮落地 %d 笔专家团已批准的订单" % approved_done)

    # ---- 专家团门控：自动交易关闭时服务端不下单，把够格的候选放进待评审队列，
    #      等外部 LLM 专家团（定时自动化）裁决；结论写回队列后由 execute_approved 成交 ----
    # ---- 专家团门控：自动交易关闭时服务端不下单，把够格的候选放进待评审队列，
    #      等外部 LLM 专家团（定时自动化）裁决；结论写回队列后由 execute_approved 成交 ----
    if not (cfg["autoTrade"] or force_trade):
        enqueue_review_candidates(s, fresh)

    done = len(s["trades"]) - before
    s["seenNews"] = list(seen)[-2000:]
    s["news"] = (fresh + s["news"])[:60]
    s["scanErr"] = err
    log("扫描完成：新增 %d 条消息，成交 %d 笔%s" % (len(fresh), done, ("（%s）" % err) if err else ""))
    save_state()
    return {"new": len(fresh), "traded": done, "err": err}
def enqueue_review_candidates(s, fresh):
    """把本轮够格的候选写入 s["pendingReview"]，供 LLM 专家团评审。

    入队规则：
      - 新闻触发的「待执行」消息：正分 -> 买入候选；负分且已持仓 -> 卖出候选
      - 无新闻但 |综合信号 delta| >= sigScore：同上按方向入队
      - 估值过滤被 block 的不入队；同股 60 分钟内评审过（reviewed 冷却）的不重复入队
      - 队列上限 6 只，避免一轮评审拖太久
      - 超时（reviewTTLHours）未评审的候选由 expire_stale_reviews() 作废出队，释放坑位
    """
    cfg = s["config"]
    pend = s.setdefault("pendingReview", [])
    reviewed = s.setdefault("reviewed", {})
    queued = {p["code"] for p in pend}
    cool = int(cfg.get("reviewCooldownMin", 60)) * 60

    def recently(code):
        ts = reviewed.get((code or "").lower())   # reviewed key 统一小写（美股 code 为大写）
        if not ts:
            return False
        try:
            return (datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds() < cool
        except Exception:
            return False

    def enqueue(item, direction, score, reason):
        code = item["code"]
        if code in queued or recently(code) or len(pend) >= 6:
            return
        sig = (s.get("signals") or {}).get(code) or {}
        if sig.get("block"):
            return
        if direction == "卖出":
            h = find_holding(s, code)
            if not h or h["shares"] <= 0:
                return
        elif int(sig.get("delta", 0) or 0) < cfg.get("buySigFloor", -6):
            return   # 信号分低于买入下限，不入队（同 execute 的买入门槛）
        pend.append({"code": code, "name": item["name"], "direction": direction,
                     "score": score, "delta": sig.get("delta", 0),
                     "reason": reason[:60], "time": now_str(),
                     "queuedAt": now_str(), "px": round(cached_price(s, code), 3),
                     "state": "waiting"})
        queued.add(code)

    by_name = {p["name"]: p for p in s["pool"]}
    for n in fresh:
        if n.get("action") != "待执行":
            continue
        sc = int(n.get("score") or 0)
        direction = "买入" if sc > 0 else "卖出"
        for nm in n.get("hits") or []:
            it = by_name.get(nm)
            if it:
                enqueue(it, direction, sc, "快讯: " + (n.get("title") or "")[:24])
    for item in s["pool"]:
        sig = (s.get("signals") or {}).get(item["code"]) or {}
        d0 = int(sig.get("delta", 0) or 0)
        if abs(d0) >= cfg.get("sigScore", 4):
            direction = "买入" if d0 > 0 else "卖出"
            enqueue(item, direction, d0,
                    "综合信号: " + " ".join(sig.get("notes", [])[:2])[:24])


def execute(s, n, hits, score, apply_delta=True):
    """按命中逐只尝试成交。apply_delta=True 时叠加综合信号修正分并应用估值硬过滤。"""
    cfg = s["config"]
    acts = []
    for name in hits:
        stop = quota_block(s)
        if stop:
            acts.append(stop)
            break
        item = None
        for p in s["pool"]:
            if p["name"] == name:
                item = p
                break
        if item is None:
            continue
        code = item["code"]
        sig = (s.get("signals") or {}).get(code) or {}
        sd = sig.get("delta", 0) if apply_delta else 0
        eff = score + sd                       # 新闻分 + 信号修正 = 决策分
        use_council = bool(cfg.get("useCouncil"))
        # 冷却：评议团模式下交给风控官判定，以便留下完整的合议记录
        if not use_council:
            last = s["lastTrade"].get(code)
            if last:
                try:
                    cool = (datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds()
                    if cool < cfg["cooldownMin"] * 60:
                        acts.append("冷却中")
                        continue
                except Exception:
                    pass
        price = current_price(s, item)
        if price <= 0:
            acts.append("无报价")
            continue

        tag = ""
        if use_council:
            # 评议团：四位分析师独立研判 -> 加权合议投票 -> 风控官一票否决
            dec = run_council(s, item, code,
                              news_score=(score if apply_delta else 0),
                              news_title=n.get("title", ""))
            if dec["verdict"] == "否决":
                acts.append("%s评议否决(%s)" % (name, dec["veto"][:16]))
                continue
            if dec["verdict"] == "观望":
                acts.append("%s评议观望%d:%d" % (name, dec["agree"], dec["total"]))
                continue
            direction = dec["verdict"]
            tag = "|评议%d:%d" % (dec["agree"], dec["oppose"])
        else:
            if abs(eff) < cfg["minScore"]:
                acts.append("%s信号抵消" % name)
                continue
            direction = "买入" if eff > 0 else "卖出"

        if apply_delta:
            base = "消息%+d信号%+d%s" % (score, sd, tag)
        else:
            base = "综合信号%+d%s" % (score, tag)
        reason = "%s:%s(%s)" % ("利好" if direction == "买入" else "利空",
                                n["title"][:12], base)
        if direction == "买入":
            if sig.get("block"):
                acts.append("%s被估值过滤" % name)
                continue
            floor = cfg.get("buySigFloor", -6)
            if int(sd) < floor:
                acts.append("%s信号分%+d<下限%+d" % (name, int(sd), floor))
                continue
            if len([h for h in s["holdings"] if h["shares"] > 0]) >= cfg["maxHold"] \
                    and find_holding(s, code) is None:
                acts.append("持仓已满")
                continue
            total = total_asset(s)
            room = total * cfg["maxPosPct"] / 100.0
            h = find_holding(s, code)
            if h:
                room -= h["price"] * h["shares"]
            budget = min(s["cash"] * cfg["buyPct"] / 100.0, room, s["cash"] * 0.98)
            qty = int(budget / price) if price > 0 else 0
            qty = int(qty)          # 美股 1 股起
            if qty < 1:
                acts.append("%s资金不足" % name)
                continue
            tr = do_buy(s, name, code, price, qty, reason)
            acts.append(("买入%d股" % tr["qty"]) if tr else "未成交")
        else:
            h = find_holding(s, code)
            if not h or h["shares"] <= 0:
                acts.append("%s未持仓" % name)
                continue
            qty = h["shares"] * cfg["sellPct"] / 100.0
            qty = max(1, int(qty))
            qty = min(qty, h["shares"])
            tr = do_sell(s, name, code, price, qty, reason)
            acts.append(("卖出%d股" % tr["qty"]) if tr else "未成交")
    if not acts:
        return "忽略"
    ok = [a for a in acts if a.startswith(("买入", "卖出"))]
    if ok:
        return ok[0] if len(ok) == 1 else "%s等%d笔" % (ok[0], len(ok))
    return acts[0]


def done_today_count(s):
    today = today_str()
    return sum(1 for t in s["trades"] if t["time"].startswith(today))


def quota_block(s):
    """执行额度检查：本轮笔数 + 当日笔数。返回拦截理由，放行返回空串。

    这两个是**执行额度**，只在真要下单时才算数：它回答「现在还能不能下单」，
    不是「这笔交易该不该做」。所以人工召集的研判会不走这里（advisory）。
    """
    cfg = s["config"]
    if int(s.get("roundTrades", 0)) >= cfg["maxPerRound"]:
        return "本轮已达%d笔上限" % cfg["maxPerRound"]
    day_cap = int(cfg.get("maxPerDay", 12))
    if done_today_count(s) >= day_cap:
        return "今日成交已达%d笔上限" % day_cap
    return ""


def current_price(s, item):
    if s["priceMode"] == "real":
        try:
            q = fetch_prices([item["code"]]).get(item["code"])
            if q and q["price"] > 0:
                return q["price"]
        except Exception:
            pass
    for h in s["holdings"]:
        if h["code"] == item["code"] and h["price"] > 0:
            return h["price"]
    return 0.0


# ---------------------------------------------------------------- HTTP
INDEX_HTML = os.path.join(BASE, "index.html")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def log_message(self, fmt, *args):
        pass

    def _account(self):
        """从 query 参数解析账户（?account=us / cn_conservative / cn_aggressive）。"""
        from urllib.parse import urlparse, parse_qs
        try:
            q = urlparse(self.path).query
            account = (parse_qs(q).get("account") or [DEFAULT_ACCOUNT])[0]
        except Exception:
            account = DEFAULT_ACCOUNT
        return account if account in ACCOUNTS else DEFAULT_ACCOUNT

    def do_GET(self):
        set_account(self._account())
        path = self.path.split("?")[0]
        with _lock:
            maybe_catchup(load_state())
        if path in ("/", "/index.html"):
            try:
                with open(INDEX_HTML, "rb") as fh:
                    self._send(200, fh.read(), "text/html; charset=utf-8")
            except Exception:
                self._send(404, "index.html not found", "text/plain; charset=utf-8")
            return
        if path == "/api/state":
            with _lock:
                self._json(load_state())
            return
        if path == "/api/health":
            self._json({"ok": True, "time": now_str()})
            return
        if path == "/api/daily_facts":
            with _lock:
                self._json(daily_facts(load_state()))
            return
        if path == "/api/version":
            self._json({"ok": True, "features": ["gate", "pendingReview", "dailyFacts", "dailyReview", "marketContext", "reviewDecoupled", "reviewTTL", "catchup"], "time": now_str()})
            return
        self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        set_account(self._account())
        path = self.path.split("?")[0]
        req = self._body()
        s = load_state()
        with _lock:
            try:
                if path == "/api/trade":
                    side = req.get("side")
                    code = req.get("code")
                    price = float(req.get("price") or 0)
                    qty = int(float(req.get("qty") or 0))
                    item = next((p for p in s["pool"] if p["code"] == code), None)
                    h = find_holding(s, code)
                    name = (item or h or {}).get("name", code)
                    if price <= 0:
                        price = current_price(s, item or {"code": code})
                    reason = (req.get("reason") or "手动")[:40]
                    tr = do_buy(s, name, code, price, qty, reason) if side == "buy" \
                        else do_sell(s, name, code, price, qty, reason)
                    save_state()
                    self._json({"ok": bool(tr), "trade": tr})
                    return

                if path == "/api/review_done":
                    # LLM 专家团评审完一只候选后调用。评审与执行解耦：
                    # 这里只把结论写回队列，成交由 scan() 在交易时段内按最新价落地。
                    code = (req.get("code") or "").strip().lower()
                    verdict = (req.get("verdict") or "").strip().upper()[:8]
                    note = (req.get("note") or "")[:120]
                    try:
                        conf = int(req.get("confidence") or 0)
                    except Exception:
                        conf = 0
                    pend = s.get("pendingReview") or []
                    hit = None
                    for p in pend:
                        if (p.get("code") or "").lower() == code:
                            hit = p
                            break
                    reviewed_at = now_str()
                    s.setdefault("reviewed", {})[code] = reviewed_at
                    if hit is None:
                        log("专家团评审 %s -> %s（%s；队列中已无此候选，多已 TTL 过期）"
                            % (code, verdict or "?", note[:40]))
                        save_state()
                        self._json({"ok": True, "queued": False, "pending": len(pend),
                                    "msg": "queue item gone (expired?)"})
                        return
                    hit["reviewedAt"] = reviewed_at
                    if verdict in ("BUY", "SELL"):
                        hit["state"] = "approved"
                        hit["review"] = {"verdict": verdict, "confidence": conf,
                                         "note": note, "reviewedAt": reviewed_at}
                        outcome = "已批准，待交易时段按最新价成交"
                    else:
                        if hit in pend:
                            pend.remove(hit)
                        outcome = "HOLD，出队"
                    # 评审历史台账：完整留存结论，独立于队列生命周期，供事后对账审计
                    rl = s.setdefault("reviewLog", [])
                    rl.append({"code": hit.get("code"), "name": hit.get("name"),
                               "verdict": verdict, "confidence": conf, "note": note,
                               "reviewedAt": reviewed_at, "outcome": outcome,
                               "queued": verdict in ("BUY", "SELL"),
                               "fillPrice": None, "fillTime": None})
                    s["reviewLog"] = rl[-200:]
                    log("专家团评审 %s(%s) -> %s 信心%s（%s）| %s"
                        % (code, hit.get("name"), verdict or "?", conf or "-",
                           note[:30], outcome))
                    done_now = 0
                    if verdict in ("BUY", "SELL") and in_trading_hours():
                        done_now = execute_approved(s)     # 盘中即时落地；盘后等下一轮
                    save_state()
                    self._json({"ok": True, "verdict": verdict,
                                "queued": verdict in ("BUY", "SELL"),
                                "executedNow": done_now,
                                "pending": len(s.get("pendingReview") or [])})
                    return
                if path == "/api/daily_review":
                    # 每日复盘：由 LLM 复盘官写入当日复盘结论（前端展示，保留最近 30 天）
                    date = (req.get("date") or datetime.now().strftime("%Y-%m-%d"))[:10]
                    summary = (req.get("summary") or "")[:600]
                    bullets = [str(b)[:160] for b in (req.get("bullets") or [])][:8]
                    lessons = [str(b)[:200] for b in (req.get("lessons") or [])][:8]
                    score = req.get("score")
                    if not summary and not bullets:
                        self._json({"ok": False, "msg": "summary/bullets 至少给一个"})
                        return
                    rec = {"date": date, "time": now_str(), "summary": summary,
                           "bullets": bullets, "lessons": lessons,
                           "score": score, "account": round(total_asset(s), 2)}
                    s["dailyReviews"] = ([rec] + [r for r in (s.get("dailyReviews") or [])
                                                  if r.get("date") != date])[:30]
                    log("每日复盘 %s 已存档（%d 条要点 / %d 条经验）"
                        % (date, len(bullets), len(lessons)))
                    save_state()
                    self._json({"ok": True, "date": date, "kept": len(s["dailyReviews"])})
                    return

                if path == "/api/market_context":
                    # 市场环境播报：由「市场环境播报」自动化收盘后写入（前端两张卡片展示）
                    # 结构：{"date":"YYYY-MM-DD", "intl":{impact,strength,summary,items[]},
                    #        "sentiment":{score,label,summary,items[]}}，items 元素 {name,value,note}
                    date = (req.get("date") or datetime.now().strftime("%Y-%m-%d"))[:10]
                    intl, sent = req.get("intl"), req.get("sentiment")
                    if not isinstance(intl, dict) or not isinstance(sent, dict):
                        self._json({"ok": False, "msg": "intl/sentiment 需为对象"})
                        return

                    def norm_items(v):
                        out = []
                        for it in (v or [])[:10]:
                            if not isinstance(it, dict):
                                continue
                            out.append({"name": str(it.get("name") or "")[:20],
                                        "value": str(it.get("value") or "")[:24],
                                        "note": str(it.get("note") or "")[:60]})
                        return out

                    sc = sent.get("score")
                    if isinstance(sc, (int, float)):
                        sc = max(-5, min(5, int(sc)))
                    rec = {
                        "date": date, "time": now_str(),
                        "intl": {"impact": str(intl.get("impact") or "")[:8],
                                 "strength": intl.get("strength"),
                                 "summary": str(intl.get("summary") or "")[:300],
                                 "items": norm_items(intl.get("items"))},
                        "sentiment": {"score": sc,
                                      "label": str(sent.get("label") or "")[:8],
                                      "summary": str(sent.get("summary") or "")[:300],
                                      "items": norm_items(sent.get("items"))},
                    }
                    if not (rec["intl"]["summary"] or rec["intl"]["items"]
                            or rec["sentiment"]["summary"] or rec["sentiment"]["items"]):
                        self._json({"ok": False, "msg": "intl/sentiment 内容为空"})
                        return
                    s["marketContext"] = rec
                    log("市场环境播报 %s 已存档（%s / 情绪 %s）"
                        % (date, rec["intl"]["impact"] or "?", rec["sentiment"]["label"] or "?"))
                    save_state()
                    self._json({"ok": True, "date": date, "time": rec["time"]})
                    return

                if path == "/api/settle":
                    pct = float(req.get("pct") or 0)
                    if s["priceMode"] == "real":
                        refresh_prices(s)
                        q = s.get("quote") or {}
                        pct = q.get("pct", pct)
                    prev = s["history"][-1]["total"] if s["history"] else s["initCapital"]
                    total = total_asset(s)
                    s["idx"] = s["idx"] * (1 + pct / 100.0)
                    s["history"].append({
                        "date": s["nextDate"], "mkt": round(pct, 4), "total": round(total, 2),
                        "dayPnl": round(total - prev, 2),
                        "ret": round((total / s["initCapital"] - 1) * 100, 4),
                        "mret": round(s["idx"] - 100, 4),
                    })
                    s["nextDate"] = next_trading_day(s["nextDate"])
                    save_state()
                    self._json({"ok": True})
                    return

                if path == "/api/inject":
                    # 手动投喂一条消息，走与自动扫描**完全相同**的决策链路
                    # （盘后/周末新闻稀少时，用它验证整条链路是否通）
                    text = (req.get("text") or "").strip()
                    if not text:
                        self._json({"ok": False, "msg": "消息内容为空"})
                        return
                    try:
                        compute_signals(s)
                    except Exception as exc:
                        log("投喂前算信号异常: %s" % exc)
                    n = {"id": "inj-" + now_str(), "time": now_str(),
                         "title": (req.get("title") or text)[:60], "text": text,
                         "inject": True}
                    if req.get("score") is not None:
                        try:
                            n["forceScore"] = int(req["score"])
                        except Exception:
                            pass
                    s["roundTrades"] = 0       # 手动投喂自成一轮
                    # 门控模式（autoTrade=False）下投喂与自动扫描保持一致：
                    # 只把候选送进评审队列，不绕过专家团直接下单
                    gate_on = not s["config"].get("autoTrade")
                    act = decide(s, n, force_trade=not gate_on)
                    if gate_on and n.get("action") == "待执行":
                        enqueue_review_candidates(s, [n])
                        act = "待专家团评审"
                    s["news"] = ([n] + list(s.get("news") or []))[:60]
                    save_state()
                    log("手动投喂 {%s} -> %s" % (n["title"][:20], act))
                    self._json({"ok": True, "action": act, "result": n})
                    return

                if path == "/api/undo":
                    if not s["history"]:
                        self._json({"ok": False, "msg": "没有可撤销的记录"})
                        return
                    r = s["history"].pop()
                    try:
                        s["idx"] = s["idx"] / (1 + r["mkt"] / 100.0)
                    except Exception:
                        pass
                    s["nextDate"] = r["date"]
                    log("撤销结算 %s" % r["date"])
                    save_state()
                    self._json({"ok": True})
                    return

                if path == "/api/refresh":
                    err = refresh_prices(s)
                    save_state()
                    self._json({"ok": True, "err": err})
                    return

                if path == "/api/scan":
                    res = scan(s, force_trade=bool(req.get("force")))
                    self._json({"ok": True, "result": res})
                    return

                if path == "/api/council":
                    # 召集专家团对全池开一次会（只出意见，不下单）
                    err = refresh_prices(s)
                    try:
                        compute_signals(s)
                    except Exception as exc:
                        log("信号计算异常: %s" % exc)
                    sess = now_str()
                    recs = []
                    for item in s["pool"]:
                        try:
                            recs.append(run_council(s, item, item["code"],
                                                    news_score=0, session=sess,
                                                    advisory=True))
                        except Exception as exc:
                            log("评议异常 %s: %s" % (item.get("name"), exc))
                    save_state()
                    self._json({"ok": True, "session": sess, "err": err, "records": recs})
                    return

                if path == "/api/pool":
                    act = req.get("action")
                    if act == "add":
                        it = {"name": req.get("name", "").strip(),
                              "code": (req.get("code") or "").strip().lower(),
                              "alias": (req.get("alias") or "").strip(),
                              "tags": (req.get("tags") or "").strip(),
                              "beta": float(req.get("beta") or 1.0)}
                        if not it["name"] or not it["code"]:
                            self._json({"ok": False, "msg": "名称和代码必填"})
                            return
                        if any(p["code"] == it["code"] for p in s["pool"]):
                            self._json({"ok": False, "msg": "该代码已在自选池"})
                            return
                        s["pool"].append(it)
                        log("自选池新增 %s(%s)" % (it["name"], it["code"]))
                    elif act == "del":
                        code = (req.get("code") or "").strip().lower()
                        s["pool"] = [p for p in s["pool"] if p["code"] != code]
                    save_state()
                    self._json({"ok": True, "pool": s["pool"]})
                    return

                if path == "/api/config":
                    for k, v in (req.get("config") or {}).items():
                        if k in s["config"]:
                            s["config"][k] = v
                    save_state()
                    self._json({"ok": True, "config": s["config"]})
                    return

                if path == "/api/mode":
                    s["priceMode"] = req.get("priceMode", s["priceMode"])
                    save_state()
                    self._json({"ok": True})
                    return

                if path == "/api/reset":
                    account = current_account()
                    keep_pool = s.get("pool")
                    _states[account] = default_state(account)
                    _states[account]["pool"] = keep_pool or list(ACCOUNTS[account]["pool"])
                    save_state()
                    self._json({"ok": True})
                    return

                self._send(404, "not found", "text/plain; charset=utf-8")
            except Exception as exc:
                self._json({"ok": False, "msg": str(exc)}, 500)


# ---------------------------------------------------------------- 定时
def worker():
    while True:
        time.sleep(30)
        for account in list(ACCOUNTS):
            set_account(account)
            try:
                s = load_state(account)
                cfg = s["config"]
                need = cfg.get("scanMin", 30) * 60
                # 频率优化：休市时降低扫描频率（至少 4 小时一次），盘中按 scanMin
                if not in_trading_hours():
                    need = max(need, 240 * 60)
                last = s.get("lastScan", "")
                due = True
                if last:
                    try:
                        delta = (datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds()
                        due = delta >= need
                    except Exception:
                        due = True
                if due:
                    if s["priceMode"] == "real":
                        refresh_prices(s)
                    # autoTrade 关闭时 scan 只刷新资讯/信号并维护待评审队列，不会下单
                    scan(s)
                    # 收盘后自动结算当天（按账户市场区分结算时间）
                    if s["priceMode"] == "real" and s["nextDate"] <= today_str():
                        now = datetime.now()
                        hm = now.hour * 60 + now.minute
                        market = (ACCOUNTS.get(account) or {}).get("market")
                        if market == "us":
                            close = 4 * 60 + 10 if us_dst(now) else 5 * 60 + 10
                            settled = hm >= close
                        else:
                            settled = now.hour > 15 or (now.hour == 15 and now.minute >= 10)
                        if settled:
                            q = s.get("quote") or {}
                            settle(s, q.get("pct", 0.0))
                            log("自动结算 %s（大盘 %+.2f%%）" % (s["history"][-1]["date"], q.get("pct", 0)))
                    s["lastScan"] = now_str()
                    save_state()
            except Exception as exc:
                try:
                    log("定时任务异常[%s]: %s" % (account, exc))
                    save_state()
                except Exception:
                    pass


def main():
    port = int(os.environ.get("PORT", "8000"))
    for account in ACCOUNTS:
        load_state(account)
    if not os.environ.get("NO_WORKER"):      # 测试时可用 NO_WORKER=1 关掉自循环
        threading.Thread(target=worker, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print("多市场模拟炒股服务已启动: http://0.0.0.0:%d" % port, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
