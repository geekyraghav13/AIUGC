"""Download the Google Noto fonts used for captions into assets/fonts/.

Usage: python scripts/get_fonts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
BASE = "https://github.com/notofonts/notofonts.github.io/raw/main/fonts"
FONTS = {
    "NotoSans-Bold.ttf": f"{BASE}/NotoSans/hinted/ttf/NotoSans-Bold.ttf",
    "NotoSansDevanagari-Bold.ttf": f"{BASE}/NotoSansDevanagari/hinted/ttf/NotoSansDevanagari-Bold.ttf",
    "NotoSansTamil-Bold.ttf": f"{BASE}/NotoSansTamil/hinted/ttf/NotoSansTamil-Bold.ttf",
    "NotoSansTelugu-Bold.ttf": f"{BASE}/NotoSansTelugu/hinted/ttf/NotoSansTelugu-Bold.ttf",
    "NotoSansBengali-Bold.ttf": f"{BASE}/NotoSansBengali/hinted/ttf/NotoSansBengali-Bold.ttf",
}
# Display fonts (Latin; Baloo 2 also has Devanagari) — one per creator style, see config/creator_styles.yaml `design`.
GF = "https://raw.githubusercontent.com/google/fonts/main/ofl"
FONTS.update({
    "Poppins-ExtraBold.ttf": f"{GF}/poppins/Poppins-ExtraBold.ttf",
    "Poppins-Bold.ttf": f"{GF}/poppins/Poppins-Bold.ttf",
    "Anton-Regular.ttf": f"{GF}/anton/Anton-Regular.ttf",
    "Montserrat-Variable.ttf": f"{GF}/montserrat/Montserrat%5Bwght%5D.ttf",
    "Baloo2-Variable.ttf": f"{GF}/baloo2/Baloo2%5Bwght%5D.ttf",
    # Devanagari-capable display fonts for Hindi/Marathi (config/creator_styles.yaml `devanagari_font`)
    "Teko-Variable.ttf": f"{GF}/teko/Teko%5Bwght%5D.ttf",
    "Hind-Bold.ttf": f"{GF}/hind/Hind-Bold.ttf",
    "Mukta-ExtraBold.ttf": f"{GF}/mukta/Mukta-ExtraBold.ttf",
    "Kalam-Bold.ttf": f"{GF}/kalam/Kalam-Bold.ttf",
    "YatraOne-Regular.ttf": f"{GF}/yatraone/YatraOne-Regular.ttf",
    # more display faces so every video can draw its own font pairing (config/typography.yaml)
    "BebasNeue-Regular.ttf": f"{GF}/bebasneue/BebasNeue-Regular.ttf",
    "Oswald-Variable.ttf": f"{GF}/oswald/Oswald%5Bwght%5D.ttf",
    "LilitaOne-Regular.ttf": f"{GF}/lilitaone/LilitaOne-Regular.ttf",
    "ArchivoBlack-Regular.ttf": f"{GF}/archivoblack/ArchivoBlack-Regular.ttf",
    "Rubik-Variable.ttf": f"{GF}/rubik/Rubik%5Bwght%5D.ttf",
    "Bangers-Regular.ttf": f"{GF}/bangers/Bangers-Regular.ttf",
    "Khand-Bold.ttf": f"{GF}/khand/Khand-Bold.ttf",
    "Rajdhani-Bold.ttf": f"{GF}/rajdhani/Rajdhani-Bold.ttf",
    "RozhaOne-Regular.ttf": f"{GF}/rozhaone/RozhaOne-Regular.ttf",
    "Sarpanch-ExtraBold.ttf": f"{GF}/sarpanch/Sarpanch-ExtraBold.ttf",
    "HindMadurai-Bold.ttf": f"{GF}/hindmadurai/HindMadurai-Bold.ttf",
    "MuktaMalar-ExtraBold.ttf": f"{GF}/muktamalar/MuktaMalar-ExtraBold.ttf",
    "BalooThambi2-Variable.ttf": f"{GF}/baloothambi2/BalooThambi2%5Bwght%5D.ttf",
    "Catamaran-Variable.ttf": f"{GF}/catamaran/Catamaran%5Bwght%5D.ttf",
    "HindGuntur-Bold.ttf": f"{GF}/hindguntur/HindGuntur-Bold.ttf",
    "MuktaVaani-ExtraBold.ttf": f"{GF}/muktavaani/MuktaVaani-ExtraBold.ttf",
    "BalooTammudu2-Variable.ttf": f"{GF}/balootammudu2/BalooTammudu2%5Bwght%5D.ttf",
    "HindSiliguri-Bold.ttf": f"{GF}/hindsiliguri/HindSiliguri-Bold.ttf",
    "BalooDa2-Variable.ttf": f"{GF}/balooda2/BalooDa2%5Bwght%5D.ttf",
})


def main() -> int:
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    failed = 0
    for name, url in FONTS.items():
        dest = FONT_DIR / name
        if dest.exists() and dest.stat().st_size > 10_000:
            print(f"ok      {name}")
            continue
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            dest.write_bytes(r.content)
            print(f"fetched {name} ({len(r.content)//1024} KB)")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAILED  {name}: {e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
