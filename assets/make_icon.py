from PIL import Image, ImageDraw
from pathlib import Path

size = 256
img = Image.new("RGBA", (size, size), "#0f172a")
draw = ImageDraw.Draw(img)

# grid
for x in range(32, size, 32):
    draw.line([(x, 20), (x, size - 20)], fill="#1f2937", width=1)
for y in range(20, size, 32):
    draw.line([(20, y), (size - 20, y)], fill="#1f2937", width=1)

# axis frame
draw.rectangle([(20, 20), (size - 20, size - 20)], outline="#334155", width=3)

# trend line
points = [(28, 190), (70, 160), (110, 180), (150, 110), (200, 120), (232, 70)]
draw.line(points, fill="#22c55e", width=10, joint="curve")
for p in points[1::2]:
    draw.ellipse((p[0] - 5, p[1] - 5, p[0] + 5, p[1] + 5), fill="#22c55e")

# accent
draw.line([(28, 220), (232, 220)], fill="#38bdf8", width=4)

out = Path("assets") / "app_icon.ico"
img.save(out, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
print(out)
