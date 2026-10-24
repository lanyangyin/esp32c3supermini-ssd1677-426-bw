# text_console.py — 控制台渲染接口（4bpp 16级灰度）
import os
from text_renderer import (
    render_full, list_available_sizes,
)


def render_console(text, size=32, direction=0,
                   per_char=None, default_style=None,
                   display=True, save_to=None,
                   align='left', valign='top', line_height=1.5,
                   margin_top=20, margin_bottom=20,
                   margin_left=20, margin_right=20,
                   font_paths=None, background=None):
    """
    控制台文本渲染（多级灰度）

    参数:
      text: 字符串
      size: 字号（list_available_sizes() 中的值）
      direction: 0 / 90 / 180 / 270
      per_char: dict {字符索引(0-based): 样式 dict}
        样式字段:
          fg / bg / outline: 0~15 的灰度值 (0=黑, 15=白)
          bold / italic / underline / strikethrough: bool
      default_style: 默认样式 dict
      display: True 时刷屏
      save_to: 保存路径（如 "image.bin"）
      font_paths: 字体文件列表（可选）
      background: 背景图路径（可选）

    灰度格式:
      0~15 整数。0 是纯黑，15 是纯白。
      可以通过抖动算法在 4bpp 图上生成更平滑的视觉效果（前端处理）。

    示例:
      render_console(
          "Hello Gray World",
          size=32,
          direction=0,
          per_char={
              "0": {"fg": 0, "bold": True},
              "1": {"fg": 8, "italic": True},
              "2": {"fg": 15, "bg": 0, "underline": True},
          },
          default_style={"fg": 0, "bg": 15},
      )
    """
    bin_data = render_full(
        text, size=size, direction=direction,
        per_char=per_char, default_style=default_style,
        align=align, valign=valign,
        line_height=line_height,
        margin_top=margin_top, margin_bottom=margin_bottom,
        margin_left=margin_left, margin_right=margin_right,
        font_paths=font_paths, background=background
    )

    if save_to:
        with open(save_to, "wb") as f:
            f.write(bin_data)
        print("已保存: %s (%d bytes)" % (save_to, len(bin_data)))

    if display:
        try:
            import epd
            epd.init()

            # 4bpp 800x480：每行 400 字节，共 480 行
            W_BYTES = 800 // 2
            for y in range(480):
                start_idx = y * W_BYTES
                end_idx = start_idx + W_BYTES
                epd._display.buffer[start_idx:end_idx] = bin_data[start_idx:end_idx]

            epd.show()
            epd.sleep()
            print("已显示到屏幕")
        except ImportError:
            print("EPD 驱动不可用，跳过显示")
        except Exception as e:
            print("显示失败: %s" % e)

    return bin_data


if __name__ == "__main__":
    render_console(
        "Hello, 16-Level Gray!\nThis is SSD1677 800x480.",
        size=32,
        direction=0,
        per_char={
            "0": {"fg": 0, "bold": True},
            "7": {"fg": 10, "underline": True},
        },
        default_style={"fg": 0, "bg": 15},
        display=False,
        save_to="console_demo.bin",
    )