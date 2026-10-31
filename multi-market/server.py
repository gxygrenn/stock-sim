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
DATA_FILE = os.path.join(BASE, "data-multi.json")   # 独立数据文件，避免读到旧单账户版残留的 data.json
# 跨部署持久化快照：随代码一起进 git 仓库并部署，重新部署后若 data-multi.json 丢失则从此恢复。
SNAPSHOT_DIR = os.path.join(BASE, "snapshot")
SNAPSHOT_FILE = os.path.join(SNAPSHOT_DIR, "data-multi.snapshot.json")
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
    "approvedTTLHours": 8,   # 2026-10-15 收紧 24→8：A股只当日成交，隔夜批准一律过期重评（防风语筑式18.6h陈旧信号）
    "sameDayBuyMax": 1,      # 同一只股票当日最多自动买入次数（防评审冷却间隙里同股连买）
    "minBuyAmount": 2000,    # 2026-10-15 新增：最小下单金额（元），低于此停买保留弹药（防零股塞单）
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
    "stopLossPct": -8,   # 个股浮亏超过该比例，禁止加仓摊平
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
    "reReviewPct": -5,           # 2026-10-15 新增：浮亏触及该%强制送 LLM 复核去留（介于0与止损线之间）
    "reReviewGapH": 72,          # 同一持仓两次强制复核的最小间隔（小时）
    "reReviewWorsenPp": 3,       # 2026-10-30 新增：冷却期内浮亏再恶化该 pp 数即提前重触发复核
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
    "indexCode": "sh000001",
    "reviewCooldownMin": 30,  # 评审冷却（稳健版 60）
    "reviewTTLHours": 24,  # 候选在待评审队列里最多等这么久，超时作废出队（防坑位被长期占死）
    "approvedTTLHours": 8,   # 2026-10-15 收紧 24→8：A股只当日成交，隔夜批准一律过期重评（防风语筑式18.6h陈旧信号）
    "sameDayBuyMax": 1,      # 同一只股票当日最多自动买入次数（防评审冷却间隙里同股连买）
    "minBuyAmount": 2000,    # 2026-10-15 新增：最小下单金额（元），低于此停买保留弹药（防零股塞单）
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
    "reReviewPct": -8,           # 2026-10-15 新增：浮亏触及该%强制送 LLM 复核去留（激进版止损-15，复核线放宽）
    "reReviewGapH": 72,          # 同一持仓两次强制复核的最小间隔（小时）
    "reReviewWorsenPp": 3,       # 2026-10-30 新增：冷却期内浮亏再恶化该 pp 数即提前重触发复核
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
    "indexCode": "usINX",  # 美股版：标普500 指数（替代上证 sh000001）
    "reviewCooldownMin": 60,  # 同一只股票专家评审后多久内不再进入待评审队列
    "reviewTTLHours": 24,  # 候选在待评审队列里最多等这么久，超时作废出队（防坑位被长期占死）
    "approvedTTLHours": 12,  # 2026-10-15 收紧 24→12：隔一个美东交易时段以上的批准结论过期重评
    "sameDayBuyMax": 1,      # 同一只股票当日最多自动买入次数（防评审冷却间隙里同股连买）
    "minBuyAmount": 300,     # 2026-10-15 新增：最小下单金额（美元），低于此停买保留弹药
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
    "stopLossPct": -8,   # 个股浮亏超过该比例，禁止加仓摊平
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
    "reReviewPct": -5,           # 2026-10-15 新增：浮亏触及该%强制送 LLM 复核去留（AMD 曾-6.5%无复核）
    "reReviewGapH": 72,          # 同一持仓两次强制复核的最小间隔（小时）
    "reReviewWorsenPp": 3,       # 2026-10-30 新增：冷却期内浮亏再恶化该 pp 数即提前重触发复核
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
        "poolChanges": [],      # 待 LLM 评审的调池建议（淘汰/选入候选，门控确认后生效）
        "poolChangeLog": [],    # 调池历史台账（最近 100 条：code/name/direction/result/时间/理由）
        "lastRebalance": "",    # 最近一次生成调池建议的日期（按 rebalanceIntervalDays 节流）
        "regime": None,         # 市场环境温度计（RSRS+扩散指标+动量，进攻/中性/防守）
        "sectors": None,        # 行业轮动 RRG 结果（相对旋转图，领先/转强/转弱/落后行业）
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
    else:
        # 主存档不存在（如重新部署后被清空）→ 尝试从跨部署快照恢复
        _restore_from_snapshot(account, d)
    _states[account] = d
    return d


def _restore_from_snapshot(account, d):
    """主存档缺失时，尝试从跨部署快照恢复（重新部署后 data-multi.json 会被清空，快照随代码部署带上）。"""
    try:
        if os.path.exists(DATA_FILE):
            return
        if not os.path.exists(SNAPSHOT_FILE):
            return
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        acct_data = None
        if isinstance(saved, dict) and any(k in ACCOUNTS for k in saved):
            acct_data = saved.get(account)
        elif account == DEFAULT_ACCOUNT:
            acct_data = saved
        if not acct_data:
            return
        # 与 load_state 相同的补键逻辑：快照里的旧字段缺失时用默认补上
        for k, v in d.items():
            if k not in acct_data:
                acct_data[k] = v
        for k, v in ACCOUNTS[account]["config"].items():
            if k not in acct_data.get("config", {}):
                acct_data["config"][k] = v
        for p in acct_data.get("pool", []):
            if "tags" not in p:
                p["tags"] = ""
        d.clear()
        d.update(acct_data)
        d.setdefault("log", []).insert(0, {"time": now_str(),
                                           "msg": "已从跨部署快照恢复最近一次存档"})
    except Exception as exc:
        d.setdefault("log", []).insert(0, {"time": now_str(),
                                           "msg": "快照恢复失败(%s)，使用新账户" % exc})


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
            _write_snapshot(_states)
    except Exception as exc:
        _save_broken = True
        print("存档写入失败，转为内存模式: %s" % exc, flush=True)


def _write_snapshot(states):
    """把当前状态同步写一份到快照文件（随代码部署，跨部署持久化）。失败不影响主存档。"""
    try:
        if not os.path.isdir(SNAPSHOT_DIR):
            os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        tmp = SNAPSHOT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(states, fh, ensure_ascii=False)
        os.replace(tmp, SNAPSHOT_FILE)
    except Exception as exc:
        print("快照写入失败: %s" % exc, flush=True)


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


_kline_cache = {}      # code -> (fetch_ts, klines)，日K历史盘中不变，缓存避免多轮重复拉取
_kline_ttl = 300       # K线缓存 5 分钟


def fetch_kline(code, n=32):
    """腾讯日K（多主机兜底 + 内存缓存 + 美股交易所后缀 fallback）。

    返回 [(date, open, close, high, low, vol), ...] 旧->新。
    美股代码需带交易所后缀才能拿到完整历史：纳斯达克 .OQ、纽交所 .N。
    """
    global _kline_ok
    now = time.time()
    hit = _kline_cache.get(code)
    if hit and hit[0] + _kline_ttl > now and len(hit[1]) >= n:
        return hit[1][-n:]

    cands = [code]
    if code.startswith("us"):
        # 美股指数：标普500 在腾讯源的正确代码是 us.INX（带点号），纳指 us.IXIC
        if code == "usINX":
            cands = ["us.INX"]
        else:
            cands = [code + ".OQ", code + ".N"]

    last = None
    for try_code in cands:
        order = [_kline_ok] + [i for i in range(len(KLINE_HOSTS)) if i != _kline_ok]
        for i in order:
            try:
                d = json.loads(http_get(KLINE_HOSTS[i] % (try_code, n)))["data"].get(try_code)
                rows = (d or {}).get("qfqday") or (d or {}).get("day") or []
                if len(rows) < 20:
                    raise RuntimeError("K线不足(%d根)" % len(rows))
                if i != _kline_ok:
                    log("K线源切换 -> %s" % KLINE_HOSTS[i].split("/")[2])
                    _kline_ok = i
                klines = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
                          for r in rows]
                _kline_cache[code] = (now, klines)
                return klines
            except Exception as exc:
                last = exc
    raise RuntimeError("K线全部源不可用: %s" % last)


def tech_score(s, code):
    """技术面信号（借鉴 Sequoia-X 的形态识别：海龟突破 / 均线放量 / RPS 相对强度）。

    维度：多头排列、金叉死叉、20日新高新低、量比异动、阳线确认（防诱多）、
          相对强度 RPS、流动性过滤。返回 (分数[-3,3], 明细)。
    """
    kl = fetch_kline(code, 65)
    if len(kl) < 20:
        return 0, []
    closes = [k[2] for k in kl]
    opens = [k[1] for k in kl]
    highs = [k[3] for k in kl]
    vols = [k[5] for k in kl]
    score, notes = 0, []
    q = (s.get("poolQuote") or {}).get(code) or {}
    # 盘中实时价优先：日 K 最后一根是「昨天收盘」，用它判「20日新高/突破」会漏掉
    # 当天盘中创新高的强势股（实测英伟达盘中创历史新高，日 K 收盘价却未更新）。
    live = q.get("price") or 0
    c = live if live > 0 else closes[-1]
    o = opens[-1]
    live_up = (q.get("pct") or 0) >= 0  # 盘中实时是否上涨（替代「是否收阳」）

    # 1. 均线多头/空头排列（MA5 > MA20 > MA60）
    ma5 = sum(closes[-5:]) / 5.0
    ma20 = sum(closes[-20:]) / 20.0
    ma60 = sum(closes[-60:]) / 60.0 if len(closes) >= 60 else ma20
    if ma5 > ma20 > ma60:
        score += 1; notes.append("多头排列+1")
    elif ma5 < ma20 < ma60:
        score -= 1; notes.append("空头排列-1")

    # 2. 金叉 / 死叉（MA5 上穿/下穿 MA20，最近 3 日内）
    if len(closes) >= 21:
        ma5_p = sum(closes[-6:-1]) / 5.0
        ma20_p = sum(closes[-21:-1]) / 20.0
        if ma5_p <= ma20_p and ma5 > ma20:
            score += 1; notes.append("金叉+1")
        elif ma5_p >= ma20_p and ma5 < ma20:
            score -= 1; notes.append("死叉-1")

    # 3. 20 日新高 / 新低（用实时价，能捕捉盘中突破）
    if c >= max(highs[-20:]):
        score += 1; notes.append("20日新高+1")
    elif c <= min(closes[-20:]):
        score -= 1; notes.append("20日新低-1")

    # 4. 量比异动
    base = sum(vols[-6:-1]) / 5.0
    if base > 0 and vols[-1] > 2 * base:
        if q.get("pct", 0) >= 0:
            score += 1; notes.append("放量上涨+1")
        else:
            score -= 1; notes.append("放量下跌-1")

    # 5. 阳线/突破确认（防诱多）：创 20 日新高且盘中上涨，才是有效突破
    if c >= max(highs[-20:]) and (live_up if live > 0 else c > o):
        score += 1; notes.append("阳线突破+1")

    # 6. 相对强度 RPS（欧奈尔）：20 日 / 60 日涨幅（用实时价）
    r20 = (c / closes[-21] - 1) * 100 if len(closes) > 21 and closes[-21] else 0
    r60 = (c / closes[-61] - 1) * 100 if len(closes) > 61 and closes[-61] else 0
    if r20 > 15 and r60 > 5:
        score += 1; notes.append("相对强度高+1")
    elif r20 < -15:
        score -= 1; notes.append("相对强度弱-1")

    # 7. 流动性过滤：成交额不足（A股/美股均按元计，低于 1 亿视为流动性差）
    amount = q.get("amount", 0) or 0
    if amount > 0 and amount < 1e8:
        score -= 1; notes.append("流动性差-1")

    # 8. 旗形突破（Sequoia-X 旗形形态，美股/A股通用）：急涨旗杆 + 高位缩量整理 + 放量突破
    flag = flag_pattern(closes, highs, vols)
    if flag > 0:
        score += 1; notes.append("旗形突破+1")
    elif flag < 0:
        score -= 1; notes.append("旗形破位-1")

    # 9/10. A股专属形态（涨停洗盘 / 跌停反包），美股不适用
    if ACCOUNTS.get(current_account(), {}).get("market") == "cn":
        if limitup_washout(closes, vols):
            score += 1; notes.append("涨停洗盘+1")
        if limitdown_reversal(closes, vols):
            score += 1; notes.append("跌停反包+1")

    return max(-3, min(3, score)), notes[:8]


def flag_pattern(closes, highs, vols):
    """旗形形态识别（借鉴 Sequoia-X 旗形策略）。

    结构：旗杆 = 近 12 日内有一段急涨（单日涨幅 >8%）；
          旗面 = 随后 3~8 日高位窄幅整理（区间幅度 < 杆高的 50%，且缩量）；
          突破 = 最新价上穿旗面上沿（放量更可靠）或跌破下沿。
    返回 +1=向上突破 / -1=向下破位 / 0=无旗形或未突破。
    """
    n = len(closes)
    if n < 10:
        return 0
    # 找旗杆：从前往后扫，在 n-9 .. n-4 之间找最大单日涨幅（给旗面留至少 3 根）
    pole_i, pole_r = -1, 0.0
    for i in range(max(1, n - 9), n - 3):
        if closes[i - 1] > 0:
            r = (closes[i] / closes[i - 1] - 1) * 100
            if r > pole_r:
                pole_r, pole_i = r, i
    if pole_r < 8 or pole_i < 0:
        return 0                        # 没有足够强的旗杆
    # 旗面：旗杆之后的整理区间（3~8 日，不含最新突破日），用收盘价定义上下沿
    seg_c = closes[pole_i + 1:-1]
    if len(seg_c) < 2 or len(seg_c) > 9:
        return 0
    seg_hi = max(seg_c)
    seg_lo = min(seg_c)
    if seg_hi <= seg_lo:
        return 0
    # 旗面高度应明显小于旗杆（窄幅整理）
    pole_h = (closes[pole_i] - closes[pole_i - 1]) / closes[pole_i - 1] * 100
    flag_h = (seg_hi - seg_lo) / seg_lo * 100
    if flag_h > pole_h * 0.5:
        return 0                        # 整理太宽，不是旗形
    # 缩量确认：旗面均量 < 旗杆日量的 1.1 倍（放量突破前通常是缩量整理，留点容差）
    pole_v = vols[pole_i] if pole_i < len(vols) else 0
    flag_vols = vols[pole_i + 1:-1] if pole_i + 1 < len(vols) else []
    flag_v = sum(flag_vols) / len(flag_vols) if flag_vols else 0
    if pole_v > 0 and flag_v > pole_v * 1.1:
        return 0                        # 整理期明显放量，非旗形
    # 突破方向
    latest = closes[-1]
    if latest > seg_hi:
        return 1                        # 向上突破
    if latest < seg_lo:
        return -1                       # 向下破位
    return 0


def limitup_washout(closes, vols):
    """涨停洗盘（A股专属）：近期出现涨停后缩量回踩不破支撑，是强势洗盘。

    规则：近 12 日内有涨停（单日涨幅 >= 9.5%）；涨停后回踩（现价低于涨停收盘，
          但未跌破涨停前一日收盘的 97%）；且回踩期间缩量。
    """
    n = len(closes)
    if n < 6:
        return False
    start = max(1, n - 12)
    for i in range(start, n - 1):
        if closes[i - 1] <= 0:
            continue
        r = (closes[i] / closes[i - 1] - 1) * 100
        if r >= 9.5:
            support = closes[i - 1]     # 涨停前一日收盘做支撑
            latest = closes[-1]
            if latest < closes[i] and latest >= support * 0.97:
                # 缩量：回踩段均量 < 涨停日量的 80%
                if i < len(vols) and vols[i] > 0:
                    tail = vols[i + 1:]
                    if tail and sum(tail) / len(tail) < vols[i] * 0.8:
                        return True
            return False
    return False


def limitdown_reversal(closes, vols):
    """跌停反包（A股专属）：跌停次日放量阳线反包，超跌反转信号。

    规则：近 10 日内有跌停（单日跌幅 <= -9.5%）；次日（或近 2 日内）收阳且
          收盘价收复跌停阴线实体一半以上，且放量。
    """
    n = len(closes)
    if n < 5:
        return False
    start = max(1, n - 10)
    for i in range(start, n - 1):
        if closes[i - 1] <= 0:
            continue
        r = (closes[i] / closes[i - 1] - 1) * 100
        if r <= -9.5:
            for j in (i + 1, i + 2):
                if j >= n:
                    break
                if closes[j - 1] <= 0:
                    continue
                if closes[j] > closes[j - 1]:           # 阳线
                    body_lo = closes[i]                 # 跌停阴线实体下沿
                    body_hi = closes[i - 1]             # 阴线实体上沿
                    if closes[j] >= (body_hi + body_lo) / 2:
                        if j < len(vols) and vols[j] > vols[i] * 1.2:   # 放量
                            return True
            return False
    return False


def _linreg(xs, ys):
    """最小二乘线性回归，返回 (斜率 beta, 截距 alpha)。数据不足返回 (None, None)。"""
    n = len(xs)
    if n < 5:
        return None, None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None, None
    beta = sxy / sxx
    alpha = my - beta * mx
    return beta, alpha


def _rsrs(kl, n=18):
    """RSRS 阻力支撑相对强度（光大证券择时系列）。

    用最近 n 根日 K 的 high 对 low 做 OLS 回归，斜率 beta 衡量「上涨时低点
    抬升 vs 高点抬升」的相对强度：beta 明显 >1 表示阻力小、趋势强；<1 表示
    下跌趋势。返回 (beta, beta 的标准化 z-score, 明细文字)。
    """
    if len(kl) < n + 5:
        return None, None, "K线不足"
    highs = [k[3] for k in kl[-n:]]
    lows = [k[4] for k in kl[-n:]]
    beta, alpha = _linreg(highs, lows)
    if beta is None:
        return None, None, "回归失败"
    # 滚动 beta 序列求 z-score（用于判断 beta 处于自身历史的高/低位）
    betas = []
    for i in range(n, len(kl)):
        hh = [k[3] for k in kl[i - n:i]]
        ll = [k[4] for k in kl[i - n:i]]
        b, _ = _linreg(hh, ll)
        if b is not None:
            betas.append(b)
    z = 0.0
    if len(betas) >= 5:
        mb = sum(betas) / len(betas)
        sd = (sum((b - mb) ** 2 for b in betas) / len(betas)) ** 0.5
        if sd > 0:
            z = (beta - mb) / sd
    return beta, z, "beta=%.3f" % beta


def _regime_rsrs_score(kl):
    """把 RSRS 转成 [-1, 1] 的择时分。beta 明显 >1 且 z 为高位 → 强；<1 → 弱。"""
    beta, z, note = _rsrs(kl)
    if beta is None:
        return 0, note
    score = 0
    if beta >= 1.03:
        score += 0.6
    elif beta >= 1.0:
        score += 0.25
    elif beta <= 0.96:
        score -= 0.6
    elif beta <= 0.99:
        score -= 0.25
    # z-score 高位/低位增强：beta 处于自身历史高位则趋势可靠，低位则趋势反转
    if z > 0.8:
        score += 0.4 if beta >= 1.0 else -0.2
    elif z < -0.8:
        score += -0.4 if beta < 1.0 else 0.2
    return max(-1.0, min(1.0, score)), "%s z=%.2f" % (note, z)


def _regime_momentum_score(kl):
    """指数均线动量：MA5/MA20/MA60 多空排列 + 20 日涨幅。返回 [-1, 1]。"""
    if len(kl) < 60:
        return 0, "K线不足"
    closes = [k[2] for k in kl]
    ma5 = sum(closes[-5:]) / 5.0
    ma20 = sum(closes[-20:]) / 20.0
    ma60 = sum(closes[-60:]) / 60.0
    c = closes[-1]
    score = 0
    if ma5 > ma20 > ma60:
        score += 0.6
    elif ma5 < ma20 < ma60:
        score -= 0.6
    r20 = (c / closes[-21] - 1) * 100 if closes[-21] else 0
    if r20 > 8:
        score += 0.4
    elif r20 < -8:
        score -= 0.4
    return max(-1.0, min(1.0, score)), "MA%s 20日%+.1f%%" % (
        "多头" if ma5 > ma20 > ma60 else ("空头" if ma5 < ma20 < ma60 else "缠绕"), r20)


def _regime_breadth_score(s):
    """市场宽度（扩散指标，东北证券 NH-NL 思路）：自选池内 20 日新高占比 - 新低占比。

    用自选池（约 100 只跨行业龙头）作为全市场宽度的代理。返回 [-1, 1]。
    """
    sig = s.get("signals") or {}
    new_high = new_low = total = 0
    for item in s["pool"]:
        code = item["code"]
        notes = (sig.get(code) or {}).get("notes") or []
        joined = " ".join(notes)
        if "20日新高" in joined:
            new_high += 1
        if "20日新低" in joined:
            new_low += 1
        total += 1
    if total == 0:
        return 0, "无数据"
    nh_pct = new_high / total
    nl_pct = new_low / total
    diff = nh_pct - nl_pct
    score = max(-1.0, min(1.0, diff * 4.0))  # 差 ±25% 映射到 ±1
    return score, "新高%.0f%%/新低%.0f%%" % (nh_pct * 100, nl_pct * 100)


def market_regime(s):
    """市场环境温度计（借鉴 QuantsPlaybook 的 RSRS + 扩散指标 + 均线动量）。

    合成 [-3, 3] 的 regime 分，映射三档：>=1 进攻 / -1~1 中性 / <=-1 防守。
    结果写入 s["regime"]，供买卖决策与风控读取。
    """
    cfg = s["config"]
    if not cfg.get("useRegime"):
        s["regime"] = {"enabled": False, "score": 0, "tier": "neutral"}
        return s["regime"]
    idx = cfg.get("indexCode", "sh000001")
    detail = {}
    total = 0.0
    try:
        kl = fetch_kline(idx, 80)
        r, rnote = _regime_rsrs_score(kl)
        m, mnote = _regime_momentum_score(kl)
        detail["rsrs"] = rnote
        detail["momentum"] = mnote
        total += r + m
    except Exception as exc:
        detail["rsrs"] = "不可用: %s" % exc
        detail["momentum"] = "不可用"
        r = m = 0.0
    try:
        b, bnote = _regime_breadth_score(s)
        detail["breadth"] = bnote
        total += b
    except Exception as exc:
        detail["breadth"] = "不可用: %s" % exc
        b = 0.0
    score = max(-3.0, min(3.0, total))
    if score >= 1.0:
        tier = "attack"
    elif score <= -1.0:
        tier = "defense"
    else:
        tier = "neutral"
    s["regime"] = {"enabled": True, "score": round(score, 2), "tier": tier,
                   "detail": detail, "time": now_str()}
    # 波动率维度：与方向分独立计算，合成到 regime 的 vol 字段（供仓位缩放读取）
    if cfg.get("useVolTarget"):
        try:
            _vol_scale(s)
        except Exception as exc:
            (s.get("regime") or {})["vol"] = {"enabled": True, "scale": 1.0,
                                              "note": "不可用: %s" % exc}
    # 行业轮动 RRG：识别强势行业，供进攻档聚焦强势行业（独立于方向分）
    if cfg.get("useSectorRotation"):
        try:
            sector_rotation(s)
        except Exception as exc:
            log("行业轮动异常: %s" % exc)
    return s["regime"]


def _volatility_profile(kl):
    """波动率画像（借鉴 QuantsPlaybook 的「时变夏普 / 波动率目标」思想）。

    用指数日 K 计算：
      - 年化波动率：最近 20 日日收益率标准差 × sqrt(252)
      - 时变夏普：最近 60 日日收益均值 / 标准差（短期风险调整后收益）
    返回 (ann_vol_pct, sharpe, note)。数据不足返回 (0, 0, "K线不足")。
    """
    if len(kl) < 22:
        return 0.0, 0.0, "K线不足"
    closes = [k[2] for k in kl]
    rets = [(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 20:
        return 0.0, 0.0, "收益率不足"
    r20 = rets[-20:]
    mean = sum(r20) / len(r20)
    var = sum((r - mean) ** 2 for r in r20) / len(r20)
    std = var ** 0.5
    ann_vol = std * (252 ** 0.5) * 100.0
    # 时变夏普：用更长窗口（60 日）均值 / 20 日波动，捕捉「短期风险调整后收益」
    if len(rets) >= 60:
        m60 = sum(rets[-60:]) / len(rets[-60:])
    else:
        m60 = sum(rets) / len(rets)
    sharpe = (m60 / std) if std > 0 else 0.0
    return ann_vol, sharpe, "年化波动%.0f%% 夏普%.2f" % (ann_vol, sharpe)


def _vol_scale(s):
    """波动率自适应仓位系数（0.5 ~ 1.2）。

    年化波动越高、时变夏普越低 → 系数越小（降仓）；低波动 + 正夏普 → 放大仓位。
    结果写入 s["regime"]["vol"]，供买入仓位与总仓位上限读取。
    """
    cfg = s["config"]
    if not cfg.get("useVolTarget"):
        return 1.0
    idx = cfg.get("indexCode", "sh000001")
    try:
        kl = fetch_kline(idx, 80)
        ann_vol, sharpe, note = _volatility_profile(kl)
    except Exception as exc:
        note = "不可用: %s" % exc
        ann_vol = sharpe = 0.0
    # 映射：年化波动 15% 以下低波、15~30% 中波、30% 以上高波
    if ann_vol <= 0:
        scale = 1.0
        tier = "neutral"
    elif ann_vol < 15:
        scale = 1.0 + min(0.2, sharpe * 0.1 if sharpe > 0 else 0.0)
        tier = "low"      # 低波动：可小幅加仓（正夏普时）
    elif ann_vol <= 30:
        scale = 1.0
        tier = "mid"
    else:
        # 高波动：线性收缩，最高降 50%；时变夏普为负进一步收紧
        scale = max(0.5, 1.0 - (ann_vol - 30) / 60.0 - (0.1 if sharpe < 0 else 0.0))
        tier = "high"
    scale = round(max(0.5, min(1.2, scale)), 2)
    reg = s.get("regime") or {}
    reg["vol"] = {"enabled": True, "annVol": round(ann_vol, 1),
                  "sharpe": round(sharpe, 2), "scale": scale, "tier": tier,
                  "note": note, "time": now_str()}
    s["regime"] = reg
    return scale


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


# ---------------------------------------------------------------- 量价因子 + 行业轮动
def price_volume_factors(code):
    """量价微观结构因子（借鉴 QuantsPlaybook 的买卖压力/振幅/凸显性因子）。

    在 tech_score 的「形态」之上，补足「资金质量」维度，用于让动态调池候选更挑剔。
    返回 (质量分[-3,3], 明细[], 因子dict)。三个因子：
      1. 买卖压力（Money Flow）：上涨日量能 vs 下跌日量能失衡度。上涨日放量、下跌日缩量
         = 资金持续净流入（压力为正）；反之资金在出逃。
      2. 振幅因子（Amplitude）：近20日 (high-low)/prev_close 均值。适度振幅(2%~6%)+上涨
         才是健康换手；过低(死水)或过高(剧烈博弈)都扣分。
      3. 凸显性因子（Salience/特质）：20日超额收益的「稳定性」——用日均收益/波动(类信息比)，
         衡量这只票在大盘之外「独立走强」的质量，剔掉靠单日脉冲撑起来的伪强势。
    """
    kl = fetch_kline(code, 65)
    if len(kl) < 22:
        return 0, ["K线不足"], {}
    closes = [k[2] for k in kl]
    opens = [k[1] for k in kl]
    highs = [k[3] for k in kl]
    lows = [k[4] for k in kl]
    vols = [k[5] for k in kl]
    score, notes = 0, []

    # ---- 因子1：买卖压力（近20日） ----
    up_vol = 0.0
    down_vol = 0.0
    for i in range(-20, 0):
        chg = closes[i] - closes[i - 1]
        v = vols[i] or 0
        if chg >= 0:
            up_vol += v
        else:
            down_vol += v
    if (up_vol + down_vol) > 0:
        # 买卖压力比：上涨日量能占比，>0.5 表示资金偏多
        pressure = up_vol / (up_vol + down_vol)
    else:
        pressure = 0.5
    if pressure >= 0.60:
        score += 1; notes.append("买压偏强+1")
    elif pressure <= 0.42:
        score -= 1; notes.append("卖压偏强-1")

    # ---- 因子2：振幅因子（近20日平均日内振幅） ----
    amps = []
    for i in range(-20, 0):
        if closes[i - 1] > 0:
            amps.append((highs[i] - lows[i]) / closes[i - 1] * 100)
    avg_amp = sum(amps) / len(amps) if amps else 0
    if 2.0 <= avg_amp <= 6.0:
        score += 1; notes.append("振幅健康+1")
    elif avg_amp < 1.2:
        score -= 1; notes.append("振幅过低(死水)-1")
    elif avg_amp > 9.0:
        score -= 1; notes.append("振幅过高(剧烈博弈)-1")

    # ---- 因子3：凸显性（20日信息比：日均收益/收益波动） ----
    rets = []
    for i in range(-20, 0):
        if closes[i - 1] > 0:
            rets.append(closes[i] / closes[i - 1] - 1)
    if len(rets) >= 5:
        mean_r = sum(rets) / len(rets)
        var_r = sum((r - mean_r) ** 2 for r in rets) / len(rets)
        sd = var_r ** 0.5
        ir = mean_r / sd if sd > 0 else 0
        # 信息比(日频)年化放大约 sqrt(252)，用阈值判断：稳且强 > 0.3 算强
        if mean_r > 0 and ir > 0.3:
            score += 1; notes.append("凸显性强(稳定跑赢)+1")
        elif mean_r > 0 and ir <= 0.1:
            # 涨但波动极大 = 靠脉冲，扣分
            score -= 1; notes.append("脉冲式上涨(不稳)-1")
        elif mean_r < 0:
            score -= 1; notes.append("超额收益为负-1")

    return max(-3, min(3, score)), notes[:6], {
        "pressure": round(pressure, 3), "avgAmp": round(avg_amp, 2),
        "infoRatio": round(ir, 3), "meanRet": round(mean_r * 100, 2)}


# 行业归类：tags 首个关键词 -> 一级行业（用于 RRG 行业轮动）
_SECTOR_MAP = {
    "银行": "银行", "保险": "非银金融", "券商": "非银金融",
    "白酒": "食品饮料", "调味品": "食品饮料", "乳制品": "食品饮料", "粮油": "食品饮料",
    "啤酒": "食品饮料", "食品": "食品饮料", "饮料": "食品饮料",
    "半导体": "电子", "消费电子": "电子", "面板": "电子", "安防": "电子",
    "军工": "国防军工", "光伏": "电力设备", "锂电池": "电力设备", "新能源车": "电力设备",
    "风电": "电力设备", "储能": "电力设备", "电力": "公用事业",
    "医药": "医药生物", "中药": "医药生物", "创新药": "医药生物", "医疗": "医药生物",
    "地产": "房地产", "房地产": "房地产",
    "服务器": "计算机", "软件": "计算机", "云计算": "计算机", "算力": "计算机",
    "传媒": "传媒", "游戏": "传媒",
    "煤炭": "煤炭", "化工": "基础化工", "建材": "建筑材料", "水泥": "建筑材料",
    "工程机械": "机械设备", "汽车零部件": "汽车", "汽车": "汽车",
    "家电": "家用电器", "航空": "交通运输", "机场": "交通运输", "物流": "交通运输",
    "通信": "通信", "电信": "通信", "运营商": "通信", "5G": "通信",
    "养殖": "农林牧渔", "农业": "农林牧渔", "钢铁": "钢铁", "有色": "有色金属",
    "机械": "机械设备", "机器人": "机械设备",
}


def sector_of(item):
    """从股票 tags 推断一级行业。返回行业名，无法归类返回 '其他'。"""
    tags = (item.get("tags") or "").split(",")
    for t in tags:
        t = t.strip()
        if t in _SECTOR_MAP:
            return _SECTOR_MAP[t]
    return "其他"


def sector_rotation(s):
    """行业轮动 RRG（相对旋转图）：算各行业相对大盘的强度与动能，识别强势行业。

    思路（借鉴 QuantsPlaybook 的 RRG / 行业量价因子）：
      - 对自选池按行业归类，取行业内股票的平均 20日/60日涨幅作为行业表现。
      - 大盘用 indexCode 的同期涨幅做基准。
      - RS-Ratio（横轴）= 行业20日涨幅 - 大盘20日涨幅（相对强度）
      - RS-Momentum（纵轴）= 行业20日涨幅 - 行业60日涨幅（动能，加速/减速）
      - 四象限：领先(强+加速) / 转强(弱但加速) / 转弱(强但减速) / 落后(弱+减速)
    返回 {sectors: [{name, ratio, momentum, tier, n}], leading: [行业名...], time}。
    """
    cfg = s["config"]
    idx_code = cfg.get("indexCode", "sh000001")
    try:
        idx_kl = fetch_kline(idx_code, 65)
    except Exception:
        idx_kl = []
    if len(idx_kl) < 62:
        return None
    idx_c = [k[2] for k in idx_kl]
    idx20 = (idx_c[-1] / idx_c[-21] - 1) * 100 if idx_c[-21] else 0
    idx60 = (idx_c[-1] / idx_c[-61] - 1) * 100 if idx_c[-61] else 0

    groups = {}
    for item in s["pool"]:
        sec = sector_of(item)
        groups.setdefault(sec, []).append(item["code"])

    sectors = []
    for sec, codes in groups.items():
        r20s, r_prev20 = [], []   # r20s=近20日涨幅，r_prev20=前20日(21-40日)涨幅，同窗口比"加速度"
        for code in codes:
            try:
                kl = fetch_kline(code, 65)
            except Exception:
                kl = []
            if len(kl) < 41:
                continue
            c = [k[2] for k in kl]
            if c[-21]:
                r20s.append((c[-1] / c[-21] - 1) * 100)
            if c[-41] and c[-21]:
                # 前20日区间：从 -41 到 -21 的涨幅（同20天窗口，比近期是否加速）
                r_prev20.append((c[-21] / c[-41] - 1) * 100)
        if not r20s:
            continue
        sec20 = sum(r20s) / len(r20s)
        sec_prev = sum(r_prev20) / len(r_prev20) if r_prev20 else 0.0
        ratio = round(sec20 - idx20, 2)          # 相对强度（近20日 vs 大盘近20日）
        momentum = round(sec20 - sec_prev, 2)    # 动能：近20日涨幅 - 前20日涨幅（加速为正）
        if ratio >= 0 and momentum >= 0:
            tier = "leading"      # 领先：强且加速
        elif ratio < 0 and momentum >= 0:
            tier = "improving"    # 转强：弱但加速
        elif ratio >= 0 and momentum < 0:
            tier = "weakening"    # 转弱：强但减速
        else:
            tier = "lagging"      # 落后：弱且减速
        sectors.append({"name": sec, "ratio": ratio, "momentum": momentum,
                        "tier": tier, "n": len(codes)})

    sectors.sort(key=lambda x: (x["ratio"] + x["momentum"]), reverse=True)
    leading = [x["name"] for x in sectors if x["tier"] == "leading"]
    improving = [x["name"] for x in sectors if x["tier"] == "improving"]
    result = {"sectors": sectors, "leading": leading, "improving": improving,
              "time": now_str()}
    s["sectors"] = result
    return result


# ---------------------------------------------------------------- 动态调池
def _cn_code_prefix(f12, f13):
    """东财 6 位代码 -> 带交易所前缀的完整代码（sh/sz）。f13: 1=沪 0=深。"""
    f12 = str(f12)
    if f12[:2] in ("60", "68", "90"):
        return "sh" + f12
    if f12[:2] in ("00", "30", "20", "12"):
        return "sz" + f12
    return ("sh" if str(f13) == "1" else "sz") + f12


_RANK_CACHE = {}      # "cn" -> (fetch_ts, cands)，候选缓存，避免 30s 循环反复撞接口
_RANK_TTL = 900       # 候选缓存 15 分钟


def _rank_get(url):
    hdrs = {"User-Agent": UA["User-Agent"],
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive"}
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=12) as resp:
        return resp.read().decode("utf-8", "ignore")


def fetch_strength_candidates(limit=40):
    """强势股候选：东财 push2 涨幅榜(f3)+主力净流入榜(f62)为主，龙虎榜净买兜底。

    返回统一结构 [{code(带sh/sz), name, industry, pct, amount, turn, pe, netIn, volRatio}]。
    amount/netIn 单位元。带重试 + 15 分钟内存缓存 + 优雅降级。
    """
    now = time.time()
    hit = _RANK_CACHE.get("cn")
    if hit and hit[0] + _RANK_TTL > now:
        return hit[1]

    cands, seen = [], set()
    for fid in ("f3", "f62"):
        url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=%d&po=1&np=1"
               "&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=%s"
               "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
               "&fields=f2,f3,f5,f6,f8,f9,f10,f12,f13,f14,f20,f62,f100" % (limit, fid))
        d = None
        for _ in range(2):
            try:
                d = json.loads(_rank_get(url))
                break
            except Exception:
                time.sleep(1)
        if not d:
            continue
        for r in ((d.get("data") or {}).get("diff") or []):
            f12 = str(r.get("f12") or "")
            if not f12 or f12 in seen:
                continue
            seen.add(f12)
            try:
                pct = float(r.get("f3") or 0)
                amount = float(r.get("f6") or 0)
                turn = float(r.get("f8") or 0)
                pe = float(r.get("f9") or 0)
                net_in = float(r.get("f62") or 0)
                volr = float(r.get("f10") or 0) or 0.0
            except Exception:
                continue
            cands.append({"code": _cn_code_prefix(f12, r.get("f13")),
                          "name": str(r.get("f14") or "").strip(),
                          "industry": str(r.get("f100") or "").strip(),
                          "pct": pct, "amount": amount, "turn": turn,
                          "pe": pe, "netIn": net_in, "volRatio": volr})

    if len(cands) < 5:
        # 兜底：龙虎榜净买为正的票（datacenter 域，比 push2 稳），补腾讯行情字段
        try:
            lhb = fetch_lhb()
        except Exception:
            lhb = {}
        top = sorted(lhb.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        codes = []
        for c6, net in top:
            if net <= 0:
                continue
            full = ("sh" if c6[:1] in ("6", "9") else "sz") + c6
            codes.append((full, net))
        if codes:
            quotes = fetch_prices([c for c, _ in codes])
            for full, net in codes:
                q = quotes.get(full) or {}
                if not q.get("price"):
                    continue
                cands.append({"code": full, "name": q.get("name", full),
                              "industry": "", "pct": q.get("pct", 0),
                              "amount": q.get("amount", 0), "turn": q.get("turn", 0),
                              "pe": q.get("pe", 0), "netIn": net, "volRatio": 0.0})

    _RANK_CACHE["cn"] = (now, cands)
    return cands


def _is_bad_name(name):
    """ST / 上市首日(N) / 次新(C) / 未盈利(-U) / 同股不同权(-W) 不纳入候选。"""
    n = (name or "").upper()
    return (n.startswith(("N", "C")) or "ST" in n or "-U" in n or "-W" in n)


def pool_remove_candidates(s):
    """分析当前自选池，找出「没前景」应淘汰的股票。

    命中任一即建议淘汰（理由按优先级）：
      - PE < 0：亏损
      - PE > 80：估值过热
      - 成交额 < 1 亿：流动性枯竭
      - 60 日跌幅 < -30%：长期走弱
      - 60 日跌幅 < -20% 且空头排列：趋势崩溃
    """
    out = []
    for item in s["pool"]:
        code = item["code"]
        if code.startswith("us"):
            continue
        q = (s.get("poolQuote") or {}).get(code) or {}
        reasons = []
        pe = q.get("pe", 0) or 0
        if pe < 0:
            reasons.append("亏损(PE为负)")
        elif pe > 80:
            reasons.append("PE%.0f 估值过热" % pe)
        amount = q.get("amount", 0) or 0
        if amount > 0 and amount < 1e8:
            reasons.append("成交额%.0f万 流动性枯竭" % (amount / 1e4))
        try:
            kl = fetch_kline(code, 61)
        except Exception:
            kl = []
        if len(kl) >= 61:
            closes = [k[2] for k in kl]
            r60 = (closes[-1] / closes[-61] - 1) * 100 if closes[-61] else 0
            ma5 = sum(closes[-5:]) / 5.0
            ma20 = sum(closes[-20:]) / 20.0
            ma60 = sum(closes[-60:]) / 60.0
            if r60 < -30:
                reasons.append("60日跌幅%.0f%%" % r60)
            elif r60 < -20 and ma5 < ma20 < ma60:
                reasons.append("60日跌幅%.0f%% 且空头排列" % r60)
        if not reasons:
            continue
        out.append({"code": code, "name": item["name"], "reasons": reasons[:3],
                    "pe": pe, "amount": amount})
    return out


def build_rebalance(s):
    """生成一轮调池建议写入 s['poolChanges']（门控：等 LLM 评审确认后落地）。"""
    cfg = s["config"]
    if not cfg.get("useRebalance"):
        return {"generated": 0, "msg": "调池未启用"}
    if not (s.get("poolQuote")):
        try:
            refresh_prices(s)
        except Exception:
            pass
    existing = {p["code"] for p in s["pool"]}
    pend = s.setdefault("poolChanges", [])
    pend_codes = {p["code"] for p in pend}
    logbook = {r.get("code"): r for r in (s.get("poolChangeLog") or [])}
    cool_days = int(cfg.get("rebalanceCooldownDays", 21))
    now = datetime.now()

    def cooled(code):
        r = logbook.get(code)
        if not r or not r.get("time"):
            return True
        try:
            return (now - datetime.strptime(r["time"][:10], "%Y-%m-%d")).days >= cool_days
        except Exception:
            return True

    max_add = int(cfg.get("rebalanceMaxAdd", 6))
    max_remove = int(cfg.get("rebalanceMaxRemove", 6))

    # ---- 淘汰建议 ----
    n_remove = 0
    for r in pool_remove_candidates(s):
        if r["code"] in pend_codes or not cooled(r["code"]):
            continue
        if n_remove >= max_remove:
            break
        pend.append({"code": r["code"], "name": r["name"], "direction": "remove",
                     "reason": "；".join(r["reasons"]), "score": 0,
                     "queuedAt": now_str(), "state": "waiting"})
        pend_codes.add(r["code"])
        n_remove += 1

    # ---- 选入建议 ----
    try:
        cands = fetch_strength_candidates(40)
    except Exception as exc:
        log("强势榜抓取失败: %s" % exc)
        cands = []
    n_add = 0
    for c in cands:
        if c["code"] in existing or c["code"] in pend_codes:
            continue
        if not cooled(c["code"]) or _is_bad_name(c["name"]):
            continue
        if c["amount"] < 1e8:                # 流动性
            continue
        if not (3 <= c["turn"] <= 20):       # 活跃但不爆炒
            continue
        if c["pe"] <= 0 or c["pe"] > 80:     # 盈利且不过热
            continue
        try:
            kl = fetch_kline(c["code"], 61)
        except Exception:
            kl = []
        if len(kl) < 61:
            continue                          # 上市不足 60 日，剔除次新
        closes = [k[2] for k in kl]
        r60 = (closes[-1] / closes[-61] - 1) * 100 if closes[-61] else 0
        if r60 < 5:
            continue                          # 60 日趋势未走强
        if n_add >= max_add:
            break
        # ---- 量价因子增强：候选除资金流向外，量价微观结构也必须健康 ----
        try:
            pvf, pvf_notes, pvf_meta = price_volume_factors(c["code"])
        except Exception:
            pvf, pvf_notes, pvf_meta = 0, [], {}
        # 行业轮动：进攻档聚焦强势行业，弱势行业候选降权（可选）
        sector_bonus = 0.0
        if cfg.get("useSectorRotation"):
            sec = c.get("industry") or "?"
            if sec and sec in (s.get("sectors") or {}).get("leading", []):
                sector_bonus = 0.6
            elif sec and sec in (s.get("sectors") or {}).get("improving", []):
                sector_bonus = 0.3
        # 量价质量分作为硬门槛：pvf < 0 的候选（微观结构差）直接剔除
        if pvf < 0:
            continue
        score = (c["netIn"] / 1e8 * 1.0 + c["pct"] * 0.3
                 + pvf * 0.5 + sector_bonus)
        pend.append({"code": c["code"], "name": c["name"], "direction": "add",
                     "reason": "主力净流入%.2f亿 60日%.0f%% 量价%d分%s"
                               % (c["netIn"] / 1e8, r60, pvf,
                                  (" 行业%s" % c["industry"]) if c.get("industry") else ""),
                     "score": round(score, 2),
                     "meta": {"industry": c["industry"], "pct": c["pct"],
                              "turn": c["turn"], "pe": c["pe"],
                              "pvf": pvf, "pvfNotes": pvf_notes},
                     "queuedAt": now_str(), "state": "waiting"})
        pend_codes.add(c["code"])
        n_add += 1

    s["lastRebalance"] = today_str()
    save_state()
    log("生成调池建议：淘汰 %d 只 / 选入 %d 只（待 LLM 门控确认）" % (n_remove, n_add))
    return {"generated": n_remove + n_add, "remove": n_remove, "add": n_add}


def execute_approved_pool_changes(s):
    """落地 LLM 已批准的调池建议：remove 移出监控池（不强制平仓）、add 补全字段入池。"""
    pend = s.get("poolChanges") or []
    if not pend:
        return 0
    logbook = s.setdefault("poolChangeLog", [])
    done = 0
    keep = []
    for p in pend:
        if (p.get("state") or "") != "approved":
            keep.append(p)
            continue
        code, name, direction = p["code"], p["name"], p["direction"]
        if direction == "remove":
            before = len(s["pool"])
            s["pool"] = [x for x in s["pool"] if x["code"] != code]
            removed = len(s["pool"]) < before
            logbook.append({"code": code, "name": name, "direction": "remove",
                            "result": "removed" if removed else "not_in_pool",
                            "time": now_str(), "note": p.get("reason", "")})
            if removed:
                log("调池落地：淘汰 %s(%s)（%s）" % (name, code, p.get("reason", "")))
        else:
            if any(x["code"] == code for x in s["pool"]):
                logbook.append({"code": code, "name": name, "direction": "add",
                                "result": "already_in_pool", "time": now_str(),
                                "note": p.get("reason", "")})
            else:
                meta = p.get("meta") or {}
                s["pool"].append({"name": name, "code": code, "alias": name,
                                  "beta": 1.0,
                                  "tags": "%s,热点,强势" % (meta.get("industry") or "")})
                # 入池即补拉现价进 poolQuote：否则新票要等下一次 refresh_prices 才有价，
                # 若赶上休市/扫描停摆，评审时 priceAtReview 会落空（2026-10-10 已实锤 A 股误杀归因缺价）
                try:
                    _q = fetch_prices([code]).get(code)
                    if _q and (_q.get("price") or 0) > 0:
                        s.setdefault("poolQuote", {})[code] = _q
                except Exception:
                    pass
                logbook.append({"code": code, "name": name, "direction": "add",
                                "result": "added", "time": now_str(),
                                "note": p.get("reason", "")})
                log("调池落地：选入 %s(%s)（%s）" % (name, code, p.get("reason", "")))
        done += 1
    s["poolChanges"] = keep
    s["poolChangeLog"] = logbook[-100:]
    return done


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
    # 行业轮动：先算一次 RRG，供 delta 合成里的行业加分用（scan 里 market_regime 也会再算，幂等）
    if cfg.get("useSectorRotation"):
        try:
            sector_rotation(s)
        except Exception as exc:
            log("行业轮动异常: %s" % exc)
    sec_leading = set((s.get("sectors") or {}).get("leading") or [])
    sec_improving = set((s.get("sectors") or {}).get("improving") or [])
    _sec_list = (s.get("sectors") or {}).get("sectors") or []
    sec_weakening = {x["name"] for x in _sec_list if x.get("tier") == "weakening"}
    sec_lagging = {x["name"] for x in _sec_list if x.get("tier") == "lagging"}
    tech_fail = 0
    pvf_fail = 0
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
        # 量价微观结构因子：直接进 delta（折半叠加，[-3,3]→[-1.5,1.5]，避免喧宾夺主）
        pvf = 0.0
        pvf_notes = []
        if cfg.get("usePriceVolume", True):
            try:
                pvf_raw, pvf_notes, _ = price_volume_factors(code)
                pvf = round(pvf_raw * 0.5, 2)
            except Exception as exc:
                pvf_fail += 1
                if pvf_fail == 1:
                    log("量价因子获取失败(%s): %s" % (code, exc))
        # 行业轮动加分：领先/转强行业加分、转弱/落后行业减分，直接进 delta（小权重）
        sec = sector_of(item)
        sector_bonus = 0.0
        if cfg.get("useSectorRotation"):
            if sec in sec_leading:
                sector_bonus = 0.4
                pvf_notes.append("领先行业+0.4")
            elif sec in sec_improving:
                sector_bonus = 0.2
                pvf_notes.append("转强行业+0.2")
            elif sec in sec_weakening:
                sector_bonus = -0.2
                pvf_notes.append("转弱行业-0.2")
            elif sec in sec_lagging:
                sector_bonus = -0.4
                pvf_notes.append("落后行业-0.4")
        delta = max(-6, min(6, tech + ls + ascore + vdelta + pvf + sector_bonus))
        notes = tnotes + ([lnote] if lnote else []) + anotes + vnotes + pvf_notes
        signals[code] = {"tech": tech, "lhb": ls, "ann": ascore, "delta": delta,
                         "pvf": pvf, "sectorBonus": sector_bonus,
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

    美股账户特殊处理：主题词（tags）过于宽泛（AI/半导体/消费/通信…），
    中文快讯里 A股/港股/日股新闻动辄命中导致「张冠李戴」，故美股只认
    名称/别名/代码，不认主题词，宁可少抓也要抓准。
    """
    hits, keys = [], []
    head = text if title is None else title
    is_us = bool(pool) and str(pool[0].get("code", "")).startswith("us")
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
            if is_us:
                continue          # 美股不靠主题词匹配，杜绝 A股/港股/日股新闻误命中
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
    # 2026-10-15 教训（激进版）：现金耗尽后仍通过 88~302 元的零股买单，无资金意义
    # 却承担个股风险。最小下单金额校验：低于门槛直接拒单，保留弹药。
    min_amt = float(s.get("config", {}).get("minBuyAmount", 0) or 0)
    if min_amt > 0 and amount < min_amt:
        log("拒单 %s：金额 %.0f 低于最小下单金额 %.0f，保留弹药" % (name, amount, min_amt))
        return None
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


# 2026 年 A 股法定节假日休市日期（上交所官方公告；周末不在此列，由 weekday 判断处理）。
# 仅覆盖 2026 年，跨年后需人工续补。
CN_HOLIDAYS_2026 = {
    # 元旦：1/1(四)~1/3(六)
    "2026-01-01", "2026-01-02", "2026-01-03",
    # 春节：2/15(日)~2/23(一)
    "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
    "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
    # 清明：4/4(六)~4/6(一)
    "2026-04-04", "2026-04-05", "2026-04-06",
    # 劳动节：5/1(五)~5/5(二)
    "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
    # 端午：6/19(五)~6/21(日)
    "2026-06-19", "2026-06-20", "2026-06-21",
    # 中秋：9/25(五)~9/27(日)
    "2026-09-25", "2026-09-26", "2026-09-27",
    # 国庆：10/1(四)~10/7(三)
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
    "2026-10-06", "2026-10-07",
}


def is_cn_holiday(dt):
    """判断某日是否为 A 股法定节假日休市日（不含周末）。"""
    return dt.strftime("%Y-%m-%d") in CN_HOLIDAYS_2026


def next_trading_day(ds):
    y, m, d = [int(x) for x in ds.split("-")]
    cur = datetime(y, m, d) + timedelta(days=1)
    while cur.weekday() >= 5 or is_cn_holiday(cur):
        cur += timedelta(days=1)
    return cur.strftime("%Y-%m-%d")


def _closed_session_label(market, now=None):
    """该市场「最近一个已收盘交易日」的日期标签。

    2026-10-28 修正：结算标签只能由它决定。此前用 s["nextDate"]，而 nextDate 会
    因漏结算而落后于真实行情日，于是把今日收盘数据写进历史日期（cn_aggressive
    自 10-19 起、cn_conservative 自 10-22 起 history 整体错位一天）。
    """
    now = now or datetime.now()
    if market == "us":
        # 美股收盘落在北京时间次日凌晨，先换算回美东当日；
        # 该日若还没到收盘（美东 <16:00），最近已收盘的交易日要再退一天
        shift = 12 if us_dst(now) else 13
        et = now - timedelta(hours=shift)
        cur = datetime(et.year, et.month, et.day)
        if et.hour < 16:
            cur -= timedelta(days=1)
        for _ in range(10):
            if cur.weekday() < 5:
                break
            cur -= timedelta(days=1)
        return cur.strftime("%Y-%m-%d")
    cur = datetime(now.year, now.month, now.day)
    for _ in range(15):
        if cur.weekday() < 5 and not is_cn_holiday(cur):
            break
        cur -= timedelta(days=1)
    return cur.strftime("%Y-%m-%d")


def settle(s, mkt_pct, date_str=None):
    """结算：记录当日快照。当日盈亏 = 总资产变化（含手续费）。

    标签来源（2026-10-28 修正）：自动结算一律用「最近一个已收盘交易日」，
    **不再用 s["nextDate"]**。nextDate 落后时，拿它当标签等于把今日数据写进
    历史日期；漏掉的交易日只能留空——宁可缺行，不可错行。

    A 股法定节假日休市日不产生结算记录（跳过，只推进 nextDate）。
    同一交易日只写一条（幂等），修复「同一天被结算两次」。
    返回本次写入的日期标签；未写入返回 None。
    """
    market = (ACCOUNTS.get(current_account()) or {}).get("market") or "cn"
    explicit = bool(date_str)
    base = date_str or _closed_session_label(market)
    if not explicit and s["history"] and (s["history"][-1].get("date") or "") >= base:
        return None                      # 该交易日已结算过，幂等跳过
    # A 股账户且 base 落在法定节假日 → 跳过结算，只推进到下一个交易日
    if market != "us":
        try:
            y, m, d = [int(x) for x in base.split("-")]
            if is_cn_holiday(datetime(y, m, d)):
                s["nextDate"] = next_trading_day(base)
                return None
        except Exception:
            pass
    prev = s["history"][-1]["total"] if s["history"] else s["initCapital"]
    total = total_asset(s)
    s["idx"] = s["idx"] * (1 + mkt_pct / 100.0)
    s["history"].append({
        "date": base,
        "mkt": round(mkt_pct, 4),
        "total": round(total, 2),
        "dayPnl": round(total - prev, 2),
        "ret": round((total / s["initCapital"] - 1) * 100, 4),
        "mret": round(s["idx"] - 100, 4),
    })
    s["nextDate"] = next_trading_day(base)
    return base


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


def _update_peak(h, price):
    """更新持仓历史最高价（用于移动止盈回撤计算）。"""
    if price > (h.get("peak") or 0):
        h["peak"] = price


def check_stops(s):
    """持仓级止损/止盈纪律（借鉴 QuantsPlaybook 的组合风控思想）。

    这是对「新闻/信号驱动卖出」的补强：新闻没有利空、信号没转弱时，持仓也可能
    已经浮亏过深或回吐过多，需要**主动**离场，而不是被动等消息。三类规则：

      1. 固定止损：现价相对成本跌破 stopLossPct → 全仓清掉（砍掉深套）
      2. 移动止盈：从历史峰值回撤超过 trailPct → 卖出，锁住利润（让利润奔跑，
         但防止「坐电梯」回吐）
      3. 目标止盈：盈利达到 takeProfitPct → 分批止盈（先落袋一部分）

    返回卖出笔数。只在交易时段内执行（与 execute_approved 一致）。
    """
    cfg = s["config"]
    if not cfg.get("useStops", True):
        return 0
    if not in_trading_hours():
        return 0
    done = 0
    stop_loss = float(cfg.get("stopLossPct", -8))
    trail = float(cfg.get("trailPct", 15))
    take = float(cfg.get("takeProfitPct", 30))
    for h in list(s["holdings"]):
        if h["shares"] <= 0 or h["cost"] <= 0 or h["price"] <= 0:
            continue
        code = h["code"]
        name = h["name"]
        price = h["price"]
        cost = h["cost"]
        pnl_pct = (price - cost) / cost * 100.0

        _update_peak(h, price)
        peak = h.get("peak") or cost
        drawdown = (price - peak) / peak * 100.0  # 相对峰值回撤（负值）

        reason = ""
        qty = 0
        # 1. 固定止损：深套清仓
        if pnl_pct <= stop_loss:
            reason = "止损：浮亏%.1f%% 触及%.0f%% 线" % (pnl_pct, stop_loss)
            qty = h["shares"]
        # 2. 移动止盈：从峰值回撤过大，锁利润
        elif pnl_pct > 0 and drawdown <= -trail:
            reason = "移动止盈：从峰值回撤%.1f%% 触及%.0f%% 线（仍浮盈%.1f%%）" % (
                drawdown, trail, pnl_pct)
            qty = h["shares"]
        # 3. 目标止盈：达到目标位，分批落袋（先卖一半）
        elif pnl_pct >= take:
            reason = "目标止盈：浮盈%.1f%% 触及%.0f%% 目标，分批落袋" % (pnl_pct, take)
            qty = max(1, int(h["shares"] * 0.5))

        if qty <= 0:
            continue
        if quota_block(s):
            # 达到当日笔数上限，本笔跳过（不卖），下轮再试
            log("止损止盈暂停（%s）" % quota_block(s))
            break
        tr = do_sell(s, name, code, price, qty, reason)
        if tr:
            done += 1
            # 止损止盈清仓后，重置峰值，避免残留影响未来重新建仓
            if h["shares"] <= 0:
                h.pop("peak", None)
            log("止损止盈卖出 %s %d 股 @%.3f（%s）" % (name, qty, price, reason))
    return done


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


def _regime_tier(s):
    """当前市场环境档位：attack / neutral / defense。regime 未启用或未算过则视为 neutral。"""
    reg = s.get("regime") or {}
    if not reg.get("enabled"):
        return "neutral"
    return reg.get("tier") or "neutral"


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
        # 大盘择时：防守档收紧总仓位上限，保留弹药
        pos_cap = cfg.get("maxTotalPos", 92)
        if _regime_tier(s) == "defense":
            pos_cap = min(pos_cap, cfg.get("regimePosCap", 70))
        # 波动率自适应：高波动降仓位上限、低波动可放宽（乘 volScale）
        if cfg.get("useVolTarget"):
            vs = (s.get("regime") or {}).get("vol") or {}
            vol_scale = float(vs.get("scale", 1.0) or 1.0)
            if vol_scale < 1.0:
                pos_cap = min(pos_cap, max(40.0, pos_cap * vol_scale))
            elif vol_scale > 1.0:
                pos_cap = min(cfg.get("maxTotalPos", 92) + 6, pos_cap * vol_scale)
        if mv / total * 100.0 >= pos_cap:
            return "总仓位超%.0f%%（防守档收紧），保留弹药" % pos_cap
        if s["cash"] < price * 1:
            return "现金不足一手"
    else:
        if not h or h["shares"] <= 0:
            return "未持仓，无可卖"
    return ""


def run_council(s, item, code, news_score=0, news_title="", skip_risk=False, record=True,
                session="", advisory=False, price_hint=0.0):
    """开一次专家会：四位分析师独立研判 -> 加权合议 -> 风控官否决。

    advisory=True 时按「研判会」处理：只出意见不下单，风控官不查执行额度。
    price_hint>0 时用该价研判（入队初审用缓存价，省一次实时拉价）。
    返回 {verdict(买入/卖出/观望/否决), score, agree, total, veto, votes[], time}。
    """
    cfg = s["config"]
    sig = (s.get("signals") or {}).get(code) or {}
    q = (s.get("poolQuote") or {}).get(code) or {}
    price = float(price_hint or 0) or current_price(s, item) or 0

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
        "reviewed": s.get("reviewed") or {},   # 完整 code->时间戳（冷却判断用，跨天也可靠）
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
    """A 股连续竞价时段（工作日 09:30-11:30 / 13:00-15:00），法定节假日休市。"""
    if now.weekday() >= 5 or is_cn_holiday(now):
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


def _next_session_open(dt, market=None):
    """dt 之后（含）的第一个交易时段开始时刻；dt 本身在盘中则原样返回。"""
    market = market or ((ACCOUNTS.get(current_account()) or {}).get("market") or "cn")
    cur = dt
    for _ in range(20):
        if market == "us":
            hm = cur.hour * 60 + cur.minute
            early_cut = 4 * 60 if us_dst(cur) else 5 * 60
            # 凌晨时段承接前一交易日晚间开盘，故周六凌晨仍算盘中（美股周五场）
            if hm < early_cut and 1 <= cur.weekday() <= 5:
                return cur
            if cur.weekday() < 5:
                if hm >= (21 * 60 + 30 if us_dst(cur) else 22 * 60 + 30):
                    return cur
                return cur.replace(hour=(21 if us_dst(cur) else 22), minute=30,
                                   second=0, microsecond=0)
        else:
            if cur.weekday() < 5 and not is_cn_holiday(cur):
                hm = cur.hour * 60 + cur.minute
                if 570 <= hm < 690 or 780 <= hm < 900:
                    return cur
                if hm < 570:
                    return cur.replace(hour=9, minute=30, second=0, microsecond=0)
                if hm < 780:
                    return cur.replace(hour=13, minute=0, second=0, microsecond=0)
        cur = (cur + timedelta(days=1)).replace(
            hour=21 if market == "us" else 9, minute=30, second=0, microsecond=0)
    return dt


def approved_age_hours(ts, market=None):
    """已批准结论的「有效年龄」：从 max(评审时刻, 下一个交易时段开盘) 起算。

    2026-10-28 修正：周末/深夜批出来的单，计时起点顺延到下一个交易时段开盘，
    避免在根本没有成交机会的时段里空转过期（T15：10-26 三账户各废 5 条，
    15 条判断零成本沉没）。盘中批的单仍按原 8h/12h 计时，隔夜陈旧结论照样作废
    ——「A股只当日成交、隔夜一律重评」的防线不变。时间戳解析失败退回墙钟口径。
    """
    market = market or ((ACCOUNTS.get(current_account()) or {}).get("market") or "cn")
    try:
        ref = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return age_hours(ts)
    start = _next_session_open(ref, market)
    return (datetime.now() - start).total_seconds() / 3600.0


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
    - 已批准（approved）：也有保质期 approvedTTLHours——超时未成交说明结论已脱离
      行情（典型：周六批准、周一才开盘，其间隔夜消息全没消化），作废出队，
      复市后的扫描会按最新信号重新入队评审，而不是拿旧结论直接下单
    - 旧格式条目（没有 queuedAt 的）一律视为已过期
    """
    cfg = s["config"]
    try:
        ttl = float(cfg.get("reviewTTLHours", 24) or 24)
    except Exception:
        ttl = 24.0
    try:
        attl = float(cfg.get("approvedTTLHours", 24) or 24)
    except Exception:
        attl = 24.0
    pend = s.get("pendingReview") or []
    if not pend:
        return 0
    keep, dropped = [], []
    for p in pend:
        st = p.get("state") or "waiting"
        if st == "approved":
            ref = p.get("reviewedAt") or p.get("queuedAt") or p.get("time")
            aged = age_hours(ref)
            # 保质期按盘中时间计（2026-10-28）：周末与夜间不计时，避免周五批的单
            # 活不过周一开盘（T15：10-26 三账户各废 5 条）
            eff = approved_age_hours(ref) if aged >= 0 else -1.0
            if aged < 0 or eff <= attl:
                keep.append(p)     # 批准未过期：交给 execute_approved 在交易时段成交
            else:
                dropped.append((p, eff, "批准结论盘中超 %.0fh 保质期" % attl))
            continue
        base = p.get("queuedAt") or p.get("time")
        aged = age_hours(base)
        if aged < 0 or aged > ttl:
            dropped.append((p, aged if aged >= 0 else ttl, "TTL 超时未评审"))
        else:
            keep.append(p)
    if not dropped:
        return 0
    s["pendingReview"] = keep
    reviewed = s.setdefault("reviewed", {})
    exp = s.setdefault("expiredReviews", [])
    for p, aged, why in dropped:
        exp.append({"code": p.get("code"), "name": p.get("name"),
                    "direction": p.get("direction"), "state": p.get("state") or "waiting",
                    "waitedH": round(aged, 1), "why": why, "time": now_str()})
        reviewed[(p.get("code") or "").lower()] = now_str()   # 记冷却（key 统一小写），避免同一条信号立刻又被塞回来
    s["expiredReviews"] = exp[-100:]
    log("评审队列作废 %d 只：%s"
        % (len(dropped), "、".join("%s(%s)" % (p.get("name") or p.get("code"), why)
                                    for p, _, why in dropped)))
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
        # 保质期按盘中时间计（2026-10-28）：墙钟 48h 若跨周末可能只等于 8h 盘中，
        # 用墙钟判会把周五收盘前的好结论误杀
        lag_eff = approved_age_hours(p.get("reviewedAt") or p.get("time")) \
            if lag >= 0 else -1.0
        # 闸门一：批准结论的保质期。过期说明结论已脱离行情（如周六批准、周一才开盘），
        # 宁可作废重评也不拿旧结论下单（2026-10-11 教训：国轩高科按 68.8h 前的结论成交）
        try:
            attl = float(cfg.get("approvedTTLHours", 24) or 24)
        except Exception:
            attl = 24.0
        if lag >= 0 and lag_eff > attl:
            s.setdefault("reviewed", {})[(p.get("code") or "").lower()] = now_str()
            drop(p, "批准结论盘中已过期 %.1fh（保质期 %.0fh，墙钟 %.1fh）"
                 % (lag_eff, attl, lag))
            continue
        lagtxt = ("评审后 %.1fh 按最新价" % lag) if lag >= 0 else "按最新价"
        reason = "专家团%s信心%s(%s):%s" % (verdict, conf or "-", lagtxt,
                                            (p.get("reason") or "")[:16])
        if verdict == "BUY":
            if sig.get("block"):
                drop(p, "已被估值过滤")
                continue
            # 闸门二：同股当日限买。评审冷却（30~60 分钟）只管「重复入队」，管不住
            # 冷却间隙里多次入队-批准-成交的连买（2026-10-12 教训：Palantir/Shopify
            # 各 2 小时内买了 3 次）。在执行咽喉统一拦截。
            day_max = int(cfg.get("sameDayBuyMax", 1) or 1)
            ckey = (p.get("code") or "").lower()
            buys_today = sum(1 for t in s.get("trades") or []
                             if (t.get("code") or "").lower() == ckey
                             and t.get("side") == "买入"
                             and (t.get("time") or "").startswith(today_str()))
            if buys_today >= day_max:
                s.setdefault("reviewed", {})[ckey] = now_str()
                drop(p, "同股当日已买 %d 次（限 %d 次/日）" % (buys_today, day_max))
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
            # 2026-10-15 教训：资金不足最小下单金额时明确弃单停买，而不是零股塞单
            min_amt = float(cfg.get("minBuyAmount", 0) or 0)
            if min_amt > 0 and budget < min_amt:
                s.setdefault("reviewed", {})[ckey] = now_str()
                drop(p, "资金不足最小下单金额 %.0f，停买保留弹药" % min_amt)
                continue
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

    # 持仓级止损/止盈纪律：基于刚刷新的现价主动离场（与新闻/信号驱动卖出互补）
    try:
        stopped = check_stops(s)
        if stopped:
            log("止损止盈触发 %d 笔" % stopped)
    except Exception as exc:
        log("止损止盈异常: %s" % exc)

    # 市场环境温度计：大盘择时（RSRS + 扩散指标 + 动量），供买卖决策/风控收紧用
    try:
        market_regime(s)
    except Exception as exc:
        log("市场择时异常: %s" % exc)

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

    def enqueue(item, direction, score, reason, is_news=False):
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
        else:
            floor = int(cfg.get("buySigFloor", -6))
            # 大盘择时：防守档上浮买入信号分下限，减少弱市里的低质候选进评审
            if _regime_tier(s) == "defense":
                floor += int(cfg.get("regimeBuyFloor", 0))
            if int(sig.get("delta", 0) or 0) < floor:
                return   # 信号分低于买入下限，不入队（同 execute 的买入门槛）
        entry = {"code": code, "name": item["name"], "direction": direction,
                 "score": score, "delta": sig.get("delta", 0),
                 "reason": reason[:60], "time": now_str(),
                 "queuedAt": now_str(), "px": round(cached_price(s, code), 3),
                 "state": "waiting"}
        # ---- 内置评议团初审：门控模式下内置评议会不再随 execute() 自动召开，
        #      导致前端「专家评议团」卡片恒空。这里让它在入队时以「初审」身份复活：
        #      合议记录写进 s["council"]（卡片有真实数据），意见附在队列条目上，
        #      LLM 专家团经 /api/state 可见，作为第二意见参考。
        #      不拦截入队——终审权仍在 LLM 专家团。----
        try:
            prev = run_council(s, item, code,
                               news_score=(score if is_news else 0),
                               news_title=(reason if is_news else ""),
                               record=True, session="门控初审",
                               advisory=True, price_hint=entry["px"])
            entry["council"] = {"verdict": prev["verdict"], "score": prev["score"],
                                "agree": prev["agree"], "oppose": prev["oppose"],
                                "abstain": prev["abstain"], "veto": prev["veto"],
                                "notes": ["%s:%s" % (v["role"], v["stance"])
                                          for v in prev["votes"]]}
        except Exception as exc:
            log("初审评议异常 %s: %s" % (item.get("name"), exc))
        pend.append(entry)
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
                enqueue(it, direction, sc, "快讯: " + (n.get("title") or "")[:24], True)
    for item in s["pool"]:
        sig = (s.get("signals") or {}).get(item["code"]) or {}
        d0 = int(sig.get("delta", 0) or 0)
        if abs(d0) >= cfg.get("sigScore", 4):
            direction = "买入" if d0 > 0 else "卖出"
            enqueue(item, direction, d0,
                    "综合信号: " + " ".join(sig.get("notes", [])[:2])[:24])

    # ---- 2026-10-15 教训（国轩/万科/AMD）：浮亏持仓长期失血却无任何动作——
    #      止损线太远时中间地带无人管。浮亏触及 reReviewPct 时主动入队一个
    #      「卖出」方向候选，交 LLM 专家团裁决 HOLD/SELL；不自动砍仓，终审权在评审。
    #      同一持仓 reReviewGapH 小时内不重复强制复核。----
    rr = float(cfg.get("reReviewPct", 0) or 0)
    if rr < 0:
        rr_gap = float(cfg.get("reReviewGapH", 72) or 72) * 3600.0
        rr_book = s.setdefault("reReviewed", {})
        # 2026-10-30 新增：记录「上次复核时的浮亏」，用于判断冷却期内是否继续恶化
        rr_pnl = s.setdefault("reReviewedPnl", {})
        for h in s["holdings"]:
            code = h.get("code") or ""
            if h.get("shares", 0) <= 0 or not code or code in queued:
                continue
            if (h.get("price") or 0) <= 0 or (h.get("cost") or 0) <= 0:
                continue
            pnl = (h["price"] - h["cost"]) / h["cost"] * 100.0
            if pnl > rr:
                continue
            ts = rr_book.get(code.lower())
            if ts:
                in_gap = False
                try:
                    in_gap = (datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds() < rr_gap
                except Exception:
                    in_gap = False
                if in_gap:
                    # 2026-10-30 修正：reReviewGapH 的本意是「别让同一只票反复刷评审」，
                    # 但 72h 冷却也把止损变成了盲区——10-30 cna 大洋电机 09:38 复核时浮亏 -5.0%
                    # 判 HOLD，收盘 -10.46%，冷却期内没有第二次复核，单日拖累 774 元。
                    # 改为：冷却期内若浮亏比上次复核时再恶化 ≥ reReviewWorsenPp（默认 3pp），
                    # 允许提前重触发。只影响「要不要送复核」，不自动下单，终审权仍在评审。
                    worsen = abs(float(cfg.get("reReviewWorsenPp", 3) or 3))
                    base_pnl = rr_pnl.get(code.lower())
                    if base_pnl is None or (float(base_pnl) - pnl) < worsen:
                        continue
            if len(pend) >= 6:
                # 2026-10-31 修正：队列满时风控项优先。此前这里是直接 break，
                # 10-30 cna 收盘队列被 1 条 approved + 5 条普通买入候选（score 2~12）占满，
                # 大洋电机跌到累计 -10.46%、越过 -8% 复核线也挤不进来，
                # 全天只有 09:38 那一次评审（当时浮亏 -5.0% 判 HOLD），单日拖累 774 元。
                # 改为挤掉分数最低的普通 waiting 候选；approved 与 forced 一律不动。
                evictable = [p for p in pend
                             if not p.get("forced") and p.get("state") == "waiting"]
                if not evictable:
                    break
                victim = min(evictable, key=lambda p: (p.get("score") or 0, p.get("queuedAt") or ""))
                pend.remove(victim)
                queued.discard(victim.get("code"))
                log("强制复核挤占队列：移除低分候选 %s（score %s）"
                    % (victim.get("name"), victim.get("score")))
            entry = {"code": code, "name": h.get("name") or code, "direction": "卖出",
                     "score": 0, "delta": 0,
                     "reason": "浮亏%.1f%%触及强制复核线%.0f%%" % (pnl, rr),
                     "time": now_str(), "queuedAt": now_str(),
                     "px": round(h.get("price") or 0, 3), "state": "waiting",
                     "forced": True}
            try:
                item = next((p for p in s["pool"] if p["code"] == code), None)
                if item:
                    prev = run_council(s, item, code, news_score=0,
                                       record=True, session="浮亏复核初审",
                                       advisory=True, price_hint=entry["px"])
                    entry["council"] = {"verdict": prev["verdict"], "score": prev["score"],
                                        "agree": prev["agree"], "oppose": prev["oppose"],
                                        "abstain": prev["abstain"], "veto": prev["veto"],
                                        "notes": ["%s:%s" % (v["role"], v["stance"])
                                                  for v in prev["votes"]]}
            except Exception as exc:
                log("浮亏复核初审异常 %s: %s" % (h.get("name"), exc))
            pend.append(entry)
            queued.add(code)
            rr_book[code.lower()] = now_str()
            rr_pnl[code.lower()] = round(pnl, 2)
            log("浮亏强制复核入队 %s（现亏%.1f%%）" % (h.get("name"), pnl))


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
            # 大盘择时：防守档上浮买入信号分下限（宁缺毋滥）
            if _regime_tier(s) == "defense":
                floor += int(cfg.get("regimeBuyFloor", 0))
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
            # 大盘择时：防守档缩小单笔买入比例
            buy_pct = cfg["buyPct"]
            if _regime_tier(s) == "defense":
                buy_pct = buy_pct * float(cfg.get("regimeBuyPctScale", 0.5))
            # 波动率自适应：高波动缩小单笔、低波动放大单笔（乘 volScale）
            if cfg.get("useVolTarget"):
                vs = (s.get("regime") or {}).get("vol") or {}
                buy_pct = buy_pct * float(vs.get("scale", 1.0) or 1.0)
            budget = min(s["cash"] * buy_pct / 100.0, room, s["cash"] * 0.98)
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
    code = item.get("code") if isinstance(item, dict) else item
    if s["priceMode"] == "real":
        try:
            q = fetch_prices([code]).get(code)
            if q and q["price"] > 0:
                return q["price"]
        except Exception:
            pass
        # 实时拉价失败 → 回退到扫描时已存的最新报价（poolQuote 里 A股/美股都常驻有价）
        pq = (s.get("poolQuote") or {}).get(code) or {}
        if (pq.get("price") or 0) > 0:
            return pq["price"]
    for h in s["holdings"]:
        if h["code"] == code and h["price"] > 0:
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
        """从 query 参数解析账户（?account=us / cn_conservative / cn_aggressive，短名 cnc/cna 亦可）。"""
        from urllib.parse import urlparse, parse_qs
        try:
            q = urlparse(self.path).query
            account = (parse_qs(q).get("account") or [DEFAULT_ACCOUNT])[0]
        except Exception:
            account = DEFAULT_ACCOUNT
        account = ACCOUNT_ALIASES.get(account, account)   # 短名别名映射（2026-10-15）
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
            self._json({"ok": True, "features": ["gate", "pendingReview", "dailyFacts", "dailyReview", "marketContext", "reviewDecoupled", "reviewTTL", "catchup", "approvedTTL", "sameDayBuyMax", "councilInitialReview", "accountAliases", "minBuyAmount", "lossReReview"], "time": now_str()})
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
                    # 2026-10-30 修正：note 从 120 字放宽到 400 字。
                    # 原因：盘中引擎 R6 要求「只数上限否决必须点名置换对象」，而置换结论通常写在
                    # note 末尾，120 字截断后只剩技术面开头，复盘无法核实规则是否执行
                    # （10-30 us 礼来/万事达四轮 HOLD 的 note 全部止于半句）。
                    note = (req.get("note") or "")[:400]
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
                    # 补记评审时价格：让「被否决/HOLD 的票后来涨跌」可回算，供每日误杀归因学习
                    # 取值优先级：入队时已缓存的 px（最稳）→ poolQuote → current_price 实时拉价
                    _rev_price = hit.get("px") or 0.0
                    if _rev_price <= 0:
                        _rev_price = cached_price(s, hit.get("code"))
                    if _rev_price <= 0:
                        _rev_item = next((it for it in s["pool"] if it.get("code") == hit.get("code")), None)
                        _rev_price = current_price(s, _rev_item) if _rev_item else 0.0
                    rl = s.setdefault("reviewLog", [])
                    rl.append({"code": hit.get("code"), "name": hit.get("name"),
                               "verdict": verdict, "confidence": conf, "note": note,
                               "reviewedAt": reviewed_at, "outcome": outcome,
                               "queued": verdict in ("BUY", "SELL"),
                               "priceAtReview": round(_rev_price, 3) if _rev_price > 0 else None,
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
                    settle(s, pct)
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

                if path == "/api/rebalance":
                    # 立即生成一轮调池建议（抓强势榜 + 分析淘汰），写入待评审队列
                    res = build_rebalance(s)
                    self._json({"ok": True, "remove": res.get("remove", 0),
                                "add": res.get("add", 0),
                                "poolChanges": s.get("poolChanges") or []})
                    return

                if path == "/api/pool_change_done":
                    # LLM 门控：写回调池建议结论 {code, action: approve|reject, note}
                    code = (req.get("code") or "").strip().lower()
                    action = (req.get("action") or "").strip().lower()
                    note = (req.get("note") or "")[:120]
                    pend = s.get("poolChanges") or []
                    hit = None
                    for p in pend:
                        if (p.get("code") or "").lower() == code:
                            hit = p
                            break
                    if hit is None:
                        save_state()
                        self._json({"ok": True, "queued": False, "pending": len(pend),
                                    "msg": "pool change item gone"})
                        return
                    if action == "approve":
                        hit["state"] = "approved"
                    else:
                        hit["state"] = "rejected"
                    hit["note"] = note
                    log("调池门控：%s %s %s(%s)（%s）"
                        % ("批准" if action == "approve" else "否决",
                           "淘汰" if hit["direction"] == "remove" else "选入",
                           hit["name"], code, note[:30]))
                    save_state()
                    done = execute_approved_pool_changes(s)   # 纯改池不涉及下单，盘内盘外均可
                    self._json({"ok": True, "approved": action == "approve",
                                "executedNow": done,
                                "pending": len(s.get("poolChanges") or [])})
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
                    # 动态调池：按周期生成建议（门控待评审）+ 落地已批准的调池
                    if s["config"].get("useRebalance"):
                        try:
                            last = s.get("lastRebalance", "")
                            due = (not last) or (
                                (datetime.now() - datetime.strptime(last, "%Y-%m-%d")).days
                                >= int(s["config"].get("rebalanceIntervalDays", 7)))
                            if due:
                                build_rebalance(s)
                            execute_approved_pool_changes(s)
                        except Exception as exc:
                            log("动态调池异常[%s]: %s" % (account, exc))
                    # 收盘后自动结算当天（按账户市场区分结算时间）
                    if s["priceMode"] == "real" and s["nextDate"] <= today_str():
                        now = datetime.now()
                        hm = now.hour * 60 + now.minute
                        market = (ACCOUNTS.get(account) or {}).get("market")
                        if market == "us":
                            # 结算窗口 = 收盘后 ~ 下一次开盘前。原写法只判 hm >= close，
                            # 而 close 只有 250/310，于是晚上 21:30 开盘时也被判「已收盘」，
                            # 拿开盘价结算当日（与「每次扫描都结算、标签逐日错位」叠加成灾）
                            close = 4 * 60 + 10 if us_dst(now) else 5 * 60 + 10
                            ope = (21 * 60 + 30) if us_dst(now) else (22 * 60 + 30)
                            settled = close <= hm < ope
                        else:
                            settled = now.hour > 15 or (now.hour == 15 and now.minute >= 10)
                        if settled:
                            q = s.get("quote") or {}
                            lab = settle(s, q.get("pct", 0.0))
                            if lab:
                                log("自动结算 %s（大盘 %+.2f%%）" % (lab, q.get("pct", 0)))
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
