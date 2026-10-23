# text_console.py — 控制台渲染接口
from text_renderer import (
    render_full, list_available_sizes, COLOR_NAMES,
)


def render_console(text, size=32, direction=0,
                   per_char=None, default_style=None,
                   display=True, save_to=None,
                   align='left', valign='top', line_height=1.5,
                   margin_top=20, margin_bottom=20,
                   margin_left=20, margin_right=20):
    """
    控制台文本渲染

    参数:
      text: 字符串
      size: 字号（list_available_sizes() 中的值）
      direction: 0 / 90 / 180 / 270
      per_char: dict {字符索引(0-based): 样式 dict}
        样式字段:
          fg / bg / outline: 颜色规格
          bold / italic / underline / strikethrough: bool
      default_style: 默认样式 dict
      display: True 时刷屏
      save_to: 保存路径（如 "image.bin"）

    颜色格式:
      "black"/"white"/"yellow"/"red"/"blue"/"green"
      "red+blue"         → 50% 伪色
      "red+blue:25"      → 25% 红 + 75% 蓝
      "red+blue:50"      → 50% / 50%
      "red+blue:75"      → 75% 红 + 25% 蓝
      [3, 4] 或 [3, 4, 8] → 索引形式（0..5，density=4/8/12）

    示例:
      render_console(
          "Hello World",
          size=32,
          direction=0,
          per_char={
              "0": {"fg": "red", "bold": True},
              "1": {"fg": "red+blue", "italic": True},
              "2": {"fg": "black", "bg": "yellow", "underline": True},
          },
          default_style={"fg": "black", "bg": "white"},
      )
    """
    bin_data = render_full(
        text, size=size, direction=direction,
        per_char=per_char, default_style=default_style,
        align=align, valign=valign,
        line_height=line_height,
        margin_top=margin_top, margin_bottom=margin_bottom,
        margin_left=margin_left, margin_right=margin_right,
    )

    if save_to:
        with open(save_to, "wb") as f:
            f.write(bin_data)
        print(f"已保存: {save_to} ({len(bin_data)} bytes)")

    if display:
        try:
            from jd7601 import JD7601
            epd = JD7601()
            epd.init()
            epd.begin_frame()
            for y in range(480):
                epd.write_chunk(bin_data[y * 360:(y + 1) * 360])
            epd.end_frame()
            epd.sleep()
            print("已显示到屏幕")
        except ImportError:
            print("EPD 驱动不可用，跳过显示")
        except Exception as e:
            print(f"显示失败: {e}")

    return bin_data


if __name__ == "__main__":
    render_console(
        "Hello, World!\nThis is E6.",
        size=32,
        direction=0,
        per_char={
            "0": {"fg": "red", "bold": True},
            "7": {"fg": "green", "underline": True},
        },
        default_style={"fg": "black", "bg": "white"},
        display=False,
        save_to="console_demo.bin",
    )