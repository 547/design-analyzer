#!/usr/bin/env python3
"""对设计截图做 OCR 文字提取 + 像素颜色采样。

用法:
  python3 ocr_analyze.py 设计图.png --crop 0,500,750,1200  [--lang chi_sim]  [--sample]
"""

import sys, os, argparse, subprocess, tempfile


def run_ocr(image_path, lang="chi_sim", psm=6):
    """运行 tesseract OCR，输出 TSV"""
    with tempfile.NamedTemporaryFile(suffix="", delete=False) as tmp:
        output_base = tmp.name

    try:
        subprocess.run(
            ["tesseract", image_path, output_base, "-l", lang, "--psm", str(psm), "tsv"],
            capture_output=True, check=True
        )
        with open(output_base + ".tsv") as f:
            return f.read()
    except subprocess.CalledProcessError as e:
        print(f"OCR 失败: {e.stderr.decode()}", file=sys.stderr)
        return ""
    finally:
        for ext in [".tsv"]:
            path = output_base + ext
            if os.path.exists(path):
                os.unlink(path)


def parse_ocr_tsv(tsv_content, min_confidence=60):
    """解析 TSV，返回按 y 排序的文字列表"""
    results = []
    for line in tsv_content.strip().split("\n")[1:]:  # 跳过表头
        cols = line.split("\t")
        if len(cols) < 12 or cols[0] != "5":  # 只取词级别
            continue
        try:
            left = float(cols[6])
            top = float(cols[7])
            width = float(cols[8])
            height = float(cols[9])
            conf = float(cols[10])
            text = cols[11].strip()
        except (ValueError, IndexError):
            continue

        if not text or conf < min_confidence:
            continue

        results.append({
            "text": text,
            "left": left,
            "top": top,
            "width": width,
            "height": height,
            "confidence": conf
        })

    results.sort(key=lambda r: r["top"])
    return results


def sample_colors(image_path, x_range, y_range, step=3):
    """像素采样，返回频次最高的颜色"""
    try:
        from PIL import Image
        from collections import Counter
    except ImportError:
        print("需要 Pillow: pip3 install Pillow", file=sys.stderr)
        return []

    img = Image.open(image_path)
    colors = []

    for y in range(y_range[0], y_range[1], step):
        for x in range(x_range[0], x_range[1], step):
            r, g, b = img.getpixel((x, y))[:3]
            if not (r > 248 and g > 248 and b > 248):  # 跳过白色
                colors.append(f"#{r:02X}{g:02X}{b:02X}")

    return Counter(colors).most_common(10)


def main():
    parser = argparse.ArgumentParser(description="设计图 OCR + 颜色分析")
    parser.add_argument("image", help="设计截图路径")
    parser.add_argument("--crop", help="裁剪区域: left,top,right,bottom")
    parser.add_argument("--lang", default="chi_sim", help="Tesseract 语言")
    parser.add_argument("--sample", action="store_true", help="是否做像素颜色采样")
    parser.add_argument("--sample-area", help="采样区域: left,top,right,bottom")
    args = parser.parse_args()

    image_path = args.image
    cropped_path = None  # 临时裁剪文件，最后清理

    try:
        if args.crop:
            try:
                from PIL import Image
            except ImportError:
                print("裁剪需要 Pillow: pip3 install Pillow", file=sys.stderr)
                sys.exit(1)

            parts = [int(x) for x in args.crop.split(",")]
            # 用 tempfile 管理临时裁剪文件
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                cropped_path = tmp.name
            img = Image.open(image_path)
            crop = img.crop(parts)
            crop.save(cropped_path)
            image_path = cropped_path

        # OCR
        tsv = run_ocr(image_path, args.lang)
        if tsv:
            words = parse_ocr_tsv(tsv)
            print("=== OCR 文字提取 ===")
            for w in words:
                flag = ""
                if w["confidence"] < 80:
                    flag = f"  ⚠️ 置信度: {w['confidence']:.0f}%"
                print(f"  '{w['text']}'  y={w['top']:.0f} x={w['left']:.0f}  {w['width']:.0f}x{w['height']:.0f}{flag}")

        # 颜色采样
        if args.sample and args.sample_area:
            parts = [int(x) for x in args.sample_area.split(",")]
            colors = sample_colors(image_path, (parts[0], parts[2]), (parts[1], parts[3]))
            if colors:
                print("\n=== 像素颜色采样 ===")
                for color, count in colors:
                    print(f"  {color}: {count} 次")

    finally:
        # 清理临时裁剪文件
        if cropped_path and os.path.exists(cropped_path):
            os.unlink(cropped_path)


if __name__ == "__main__":
    main()
