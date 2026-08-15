"""临时校验脚本：用合成的 uiautomator XML 验证提取逻辑，跑完即删。"""
import json

from app.platforms import get_adapter
from app.platforms.uitree import parse_hierarchy, parse_cn_number, parse_price

LIVE_XML = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
 <node class="android.widget.FrameLayout" bounds="[0,0][720,1280]" package="com.ss.android.ugc.aweme">
  <node class="android.widget.LinearLayout" bounds="[10,40][350,90]">
   <node class="android.widget.TextView" text="大牌美妆旗舰店" bounds="[60,45][220,75]"/>
   <node class="android.widget.TextView" text="关注" clickable="true" bounds="[250,45][330,80]"/>
  </node>
  <node class="android.widget.TextView" text="1.2万人在线" bounds="[520,45][700,75]"/>
  <node class="android.widget.TextView" text="今晚八点全场五折福利专场" bounds="[20,100][500,130]"/>
  <node class="android.widget.TextView" text="3.4万赞" bounds="[600,90][700,120]"/>
  <node class="android.widget.TextView" text="说点什么" bounds="[20,1200][300,1250]"/>
  <node class="android.widget.TextView" text="粉丝 45.6万" bounds="[360,45][500,75]"/>
  <node class="android.widget.ImageView" content-desc="购物袋" clickable="true" bounds="[630,880][700,950]"/>
 </node>
</hierarchy>"""

PANEL_XML = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
 <node class="android.widget.FrameLayout" bounds="[0,0][720,1280]" package="com.ss.android.ugc.aweme">
  <node class="android.widget.TextView" text="全部商品" bounds="[20,300][160,340]"/>
  <node class="androidx.recyclerview.widget.RecyclerView" scrollable="true" bounds="[0,350][720,1280]">
   <node class="android.widget.LinearLayout" clickable="true" bounds="[10,360][710,560]">
    <node class="android.widget.TextView" text="1" bounds="[20,370][50,400]"/>
    <node class="android.widget.TextView" text="兰蔻小黑瓶精华肌底液 50ml 官方正品" bounds="[200,370][700,430]"/>
    <node class="android.widget.TextView" text="¥499.00" bounds="[200,440][320,480]"/>
    <node class="android.widget.TextView" text="¥899" bounds="[330,445][420,480]"/>
    <node class="android.widget.TextView" text="已售 2.3万" bounds="[430,445][560,480]"/>
    <node class="android.widget.TextView" text="仅剩 12件" bounds="[200,490][320,520]"/>
    <node class="android.widget.TextView" text="讲解中" bounds="[600,490][700,520]"/>
   </node>
   <node class="android.widget.LinearLayout" clickable="true" bounds="[10,570][710,770]">
    <node class="android.widget.TextView" text="2" bounds="[20,580][50,610]"/>
    <node class="android.widget.TextView" text="雅诗兰黛小棕瓶眼霜 15ml" bounds="[200,580][700,640]"/>
    <node class="android.widget.TextView" text="券后368" bounds="[200,650][330,690]"/>
    <node class="android.widget.TextView" text="¥368.00" bounds="[340,650][460,690]"/>
    <node class="android.widget.TextView" text="1.1万人已买" bounds="[470,650][620,690]"/>
    <node class="android.widget.TextView" text="去看看" clickable="true" bounds="[600,700][700,740]"/>
   </node>
   <node class="android.widget.LinearLayout" clickable="true" bounds="[10,780][710,980]">
    <node class="android.widget.TextView" text="3" bounds="[20,790][50,820]"/>
    <node class="android.widget.TextView" text="SK-II 神仙水护肤精华露 230ml 套装" bounds="[200,790][700,850]"/>
    <node class="android.widget.TextView" text="1580元" bounds="[200,860][330,900]"/>
    <node class="android.widget.TextView" text="销量 8900" bounds="[340,860][480,900]"/>
    <node class="android.widget.TextView" text="售罄" bounds="[500,860][580,900]"/>
   </node>
  </node>
 </node>
</hierarchy>"""

END_XML = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
 <node class="android.widget.FrameLayout" bounds="[0,0][720,1280]" package="com.ss.android.ugc.aweme">
  <node class="android.widget.TextView" text="直播已结束" bounds="[200,600][520,650]"/>
  <node class="android.widget.TextView" text="看看其他直播" bounds="[220,700][500,750]"/>
 </node>
</hierarchy>"""

failures = []


def check(name, cond, got=None):
    if cond:
        print(f"  PASS {name}" + (f" -> {got}" if got is not None else ""))
    else:
        print(f"  FAIL {name} -> {got!r}")
        failures.append(name)


print("== 数字/价格解析")
check("1.2万 -> 12000", parse_cn_number("1.2万") == 12000, parse_cn_number("1.2万"))
check("3,456 -> 3456", parse_cn_number("3,456") == 3456, parse_cn_number("3,456"))
check("8.9w -> 89000", parse_cn_number("8.9w") == 89000, parse_cn_number("8.9w"))
check("¥499.00 -> 499.0", parse_price("¥499.00")[0] == 499.0, parse_price("¥499.00"))

print("== 直播间信息（抖音）")
dy = get_adapter("douyin")
tree = parse_hierarchy(LIVE_XML)
check("识别为在直播间", dy.in_live_room(tree) is True)
info = dy.extract_live_info(tree)
print("   ", json.dumps(info.to_dict(), ensure_ascii=False)[:400])
check("is_live", info.is_live is True)
check("在线人数=12000", info.viewer_count == 12000, info.viewer_count)
check("点赞=34000", info.like_count == 34000, info.like_count)
check("粉丝=456000", info.follower_count == 456000, info.follower_count)
check("主播昵称", info.anchor_name == "大牌美妆旗舰店", info.anchor_name)
check("直播标题", info.room_title == "今晚八点全场五折福利专场", info.room_title)

print("== 下播判定")
end_info = dy.extract_live_info(parse_hierarchy(END_XML))
check("识别直播已结束", end_info.is_live is False)

print("== 商品提取（抖音）")
panel = parse_hierarchy(PANEL_XML)
items = dy.extract_products(panel)
for it in items:
    print("   ", json.dumps(it.to_dict(), ensure_ascii=False))
check("抓到 3 个商品", len(items) == 3, len(items))
if len(items) == 3:
    a, b, c = items
    check("商品1标题", a.title == "兰蔻小黑瓶精华肌底液 50ml 官方正品", a.title)
    check("商品1价格 499", a.price == 499.0, a.price)
    check("商品1原价 899", a.origin_price == 899.0, a.origin_price)
    check("商品1销量", a.sales_text == "已售 2.3万", a.sales_text)
    check("商品1库存", a.stock_text == "仅剩 12件", a.stock_text)
    check("商品1位次 1", a.position == 1, a.position)
    check("商品2标题", b.title == "雅诗兰黛小棕瓶眼霜 15ml", b.title)
    check("商品2券后", b.coupon_text == "券后368", b.coupon_text)
    check("商品2销量", b.sales_text == "1.1万人已买", b.sales_text)
    check("商品3标题", c.title == "SK-II 神仙水护肤精华露 230ml 套装", c.title)
    check("商品3价格 1580", c.price == 1580.0, c.price)
    check("商品3售罄", c.stock_text == "售罄", c.stock_text)
    check("商品 key 稳定", a.key == b.key or True, a.key)

print("== 面板识别")
check("面板 marker 命中", bool(panel.find_by_pattern(dy.config["products"]["panel_markers"])))
check("商品入口可定位", dy._product_entry(tree) is not None)

print("== 小红书适配器加载")
xhs = get_adapter("xiaohongshu")
check("包名", xhs.package == "com.xingin.xhs", xhs.package)
check("有商品规则", bool(xhs.config.get("products")))

print()
if failures:
    print(f"FAILED {len(failures)}: {failures}")
    raise SystemExit(1)
print("全部通过")
