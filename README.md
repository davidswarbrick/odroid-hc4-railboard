# railboard

A live UK train **departure board** for a small I2C OLED, driven from any Linux
single-board computer (built for an **ODROID HC4**, works on a Raspberry Pi too).

It rotates through several pages on a 128×64 OLED:

- **Full departure boards** for one or more stations (time · destination · status)
- **"Next train" pages** that mirror a platform indicator for a specific journey —
  a big countdown (`6 min`), scheduled time, platform, status, and scrolling
  calling points
- A **system / disk health** page (IP address, disk usage, CPU temperature, load,
  uptime) so the display doubles as a headless-box status screen
- An optional **combo** page showing two journeys' next trains side by side

Everything (stations, journeys, page order, timings) is driven by `config.yaml`.
Active **burn-in mitigation** is built in (pixel-shift, quiet-hours dimming, optional
invert) because a departure board has a lot of static layout.

Data comes from the **Rail Data Marketplace** Live Departure Board REST API (the
modern replacement for the Darwin OpenLDBWS SOAP service).

---

## 1. Hardware

- An SBC running Linux with an I2C OLED wired up (SSD1306 or SH1106, 128×64).
- Enable I2C and find the panel address:

  ```bash
  sudo apt install i2c-tools        # or: sudo pacman -S i2c-tools
  i2cdetect -y 1                    # note the address, usually 0x3c or 0x3d
  ```

Set `display.driver`, `display.i2c_port`, and `display.i2c_address` in `config.yaml`
to match.

## 2. Get a Rail Data Marketplace API key

1. Register at <https://raildata.org.uk>.
2. In the product catalogue, subscribe to a **Live Departure Board** product
   (the free "open" tier is usually approved instantly).
3. Open the product's **Specification** tab. Copy:
   - your **consumer key** (used as the `x-apikey` header), and
   - the **product prefix** in the base URL — the path segment after the host,
     e.g. `1010-live-departure-board-dep`. It varies per subscription/version, so
     set it as `api.product_prefix` in `config.yaml`.

The full request this app makes looks like:

```
GET https://api1.raildata.org.uk/<product_prefix>/LDBWS/api/20220120/GetDepBoardWithDetails/<CRS>
Header: x-apikey: <consumer key>
```

Test it before running the app:

```bash
curl -H "x-apikey: $RDM_API_KEY" \
  "https://api1.raildata.org.uk/<product_prefix>/LDBWS/api/20220120/GetDepBoardWithDetails/KGX"
```

## 3. Install

```bash
git clone <your-fork-url> railboard && cd railboard
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# For a desktop preview window (optional):
.venv/bin/pip install luma.emulator
```

`luma.oled` is only needed on the machine with the OLED. `psutil` is optional
(the health page falls back to stdlib without it).

## 4. Configure

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml`:

- **stations** — the CRS (3-letter) code and display name of each station.
  Look codes up at
  <http://www.railwaycodes.org.uk/crs/crs0.shtm> or on nationalrail.co.uk.
- **journeys** — named `origin → target` pairs for the "next train" pages.
  `match: any` counts any train that *calls at* the target (what you'd actually
  board); `match: destination` only counts trains terminating there.
- **pages** — the rotation. Each entry is one page:
  - `board:<CRS>` — full board for a station
  - `health` — the system/disk page
  - `next:<journey id>` — platform-mirror of the next matching train
  - `combo:<id>,<id>` — two journeys on one page
- **display / burn_in / quiet_hours** — panel wiring, timings, and burn-in options.

Put your API key in the environment (not in the file):

```bash
echo 'RDM_API_KEY=your_consumer_key' > .env    # gitignored
```

## 5. Run

```bash
# On the OLED hardware:
set -a; source .env; set +a
.venv/bin/python -m railboard --config config.yaml --display real

# Desktop preview (needs luma.emulator + pygame):
.venv/bin/python -m railboard --display emulator

# Headless: render each page once to PNGs in display.simulate_dir, no key needed:
.venv/bin/python -m railboard --display simulate --mock --once
```

Flags: `--display real|emulator|simulate`, `--mock` (synthetic data, no API key),
`--once` (each page once then exit), `--log-level DEBUG`.

## 6. Run on boot (systemd)

Edit the `User`, paths, and (if needed) the venv location in `railboard.service`, then:

```bash
sudo cp railboard.service /etc/systemd/system/railboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now railboard.service
journalctl -u railboard.service -f
```

If you're replacing an existing OLED stats script, disable its service first
(`sudo systemctl disable --now <old-service>`).

## Burn-in mitigation

OLED panels can retain a static image. `railboard` mitigates this by:

- **Pixel-shift ("orbit")** — the whole frame is drawn into an inset safe area and
  nudged by ±`orbit_max` px each page change, so no pixel is lit in the same spot
  for long. This is why the usable area is slightly smaller than the panel.
- **Quiet hours** — dim (lower contrast) or fully blank the panel overnight; set
  the window and `action: dim|blank` under `quiet_hours`.
- **Content rotation** — cycling pages already avoids a fixed layout.
- **Optional periodic invert** — set `burn_in.invert_minutes` > 0.

## Fonts

By default a system font (DejaVu) is used, or Pillow's built-in bitmap font if none
is found. For an authentic dot-matrix look, point `display.font_path` at a pixel /
LED-matrix TTF and adjust `font_size`.

## Troubleshooting

- **401 / 403** — bad or unsubscribed key. Check `RDM_API_KEY` is the *consumer key*
  (not the secret) and that your subscription is approved.
- **404** — wrong `api.product_prefix` or CRS. Compare the URL in the error log with
  the base URL on your RDM Specification tab.
- **Nothing on the OLED** — wrong `i2c_address`/`driver`; confirm with `i2cdetect`.
  Try `--display simulate --mock --once` to prove the software path independently.
