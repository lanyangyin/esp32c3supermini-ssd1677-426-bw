# tone.py — 色调调整
from PIL import ImageEnhance, ImageFilter


def apply_exposure(img, value):
    """value: -1 ~ +1，亮度乘数 2^value"""
    return ImageEnhance.Brightness(img).enhance(2.0 ** value)


def apply_saturation(img, value):
    """value: -1 ~ +1，1 为原值"""
    return ImageEnhance.Color(img).enhance(1.0 + value)


def apply_contrast(img, value):
    """value: -1 ~ +1，1 为原值"""
    return ImageEnhance.Contrast(img).enhance(1.0 + value)


def apply_clarity(img, value):
    """value: 0 ~ 2，局部对比度"""
    if value <= 0:
        return img
    return img.filter(ImageFilter.UnsharpMask(radius=2, percent=int(100 * value), threshold=3))


def apply_shadow_boost(img, value):
    """value: 0 ~ 1，提亮暗部"""
    if value <= 0:
        return img
    lut = []
    for i in range(256):
        t = i / 255.0
        s = t ** (1 - value * 0.5)
        lut.append(int(s * 255))
    return img.point(lut * 3)


def apply_highlight_compress(img, value):
    """value: 0 ~ 1，压暗亮部"""
    if value <= 0:
        return img
    lut = []
    for i in range(256):
        t = i / 255.0
        s = 1 - (1 - t) ** (1 + value * 0.5)
        lut.append(int(s * 255))
    return img.point(lut * 3)


def apply_all(img, exposure=0.0, saturation=0.0, contrast=0.0,
              clarity=0.0, shadow=0.0, highlight=0.0):
    img = apply_exposure(img, exposure)
    img = apply_saturation(img, saturation)
    img = apply_contrast(img, contrast)
    img = apply_clarity(img, clarity)
    img = apply_shadow_boost(img, shadow)
    img = apply_highlight_compress(img, highlight)
    return img