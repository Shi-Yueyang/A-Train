# A-Train BTM Telegram Encoding (JSON to CTCS Balise Frame User Data)

This document specifies the JSON contract accepted by the simulator's BTM
equipment endpoint and the CTCS transponder telegram bit stream it produces,
as implemented by `src/a_train/telegram/`. Encoding happens at the adapter
edge; everything downstream (train model, BTM equipment, snapshots, ATP wire
protocol) keeps treating the payload as opaque bytes (architectural.md §7.3).

## 1. Endpoint

`POST /api/trains/{train_id}/equipment/{btm_key}` (web-api.md) with a
`telegram` object instead of the raw base64 `data` field:

```json
{
  "cab_id": 1,
  "telegram": {
    "packets": [
      { "packet": 41, "q_dir": 1, "q_scale": 0, "d_leveltr": 1234, "m_leveltr": 2, "l_ackleveltr": 5 }
    ]
  }
}
```

- `telegram` is accepted only for equipment of type `btm`; `telegram` and
  `data` are mutually exclusive.
- Any validation failure returns **HTTP 400** whose `detail` starts with the
  offending JSON field path (e.g.
  `telegram.packets[0].d_leveltr: value 32768 out of range for 15 bits (0-32767)`);
  the simulation state is unchanged.
- On success the encoded bytes are delivered exactly like a `data` payload:
  `payload_b64` in the equipment snapshot and in the next `TRAIN_STATE`
  (atp-api.md §3.1).
- The raw `data` base64 field remains the authoritative escape hatch for
  test vectors, vendor formats, or anything this encoder cannot express.

All field values are **raw coded integers** exactly as printed in the frame
and packet tables. The encoder validates bit widths and framing only; it
does not apply enums, unit offsets, or scaling.

## 2. Frame layout

Fixed 830-bit frame (104 output bytes), MSB first, transmission order left
to right:

| Section                    | Bits | Notes                                          |
| -------------------------- | ---: | ---------------------------------------------- |
| Frame header (§3)          |   50 | `Q_UPDOWN` … `Q_LINK`                          |
| User packet area (§4)       |  772 | packets in caller order, zero-padded           |
| Information end            |    8 | `1111 1111` marks the end of the frame         |

- Each packet is self-delimiting: `NID_PACKET` (8 bits) + `L_PACKET`
  (13 bits, counting the packet bits that follow it) + packet fields.
- Content that does not fit the 772-bit user packet area is rejected.
- Output padding: the frame is already a whole number of bytes (830 bits →
  104 bytes; the last 6 bits belong to the zero padding).
- Air-interface channel coding (scrambling, 10-to-11 symbols, check bits)
  is **not** applied: a BTM delivers unshaped frame user data to ATP.

## 3. Header fields

All 50 header bits in transmission order; every field is settable, with the
defaults below encoding an uplink balise telegram.

| Field        | Bits | Default | Notes                                                          |
| ------------ | ---: | ------: | -------------------------------------------------------------- |
| `q_updown`   |    1 |       1 | Direction: `0` = train-to-track (车对地), `1` = track-to-train (地对车). |
| `m_version`  |    7 |       3 | Language/code version (`0010000` = V1.0).                      |
| `q_media`    |    1 |       0 | Medium: `0` = balise (应答器), `1` = loop (环线).              |
| `n_pig`      |    3 |       0 | Position in group, offset-coded: `000` = 1st … `111` = 8th.    |
| `n_total`    |    3 |       0 | Balises in group, offset-coded: `000` = 1 … `111` = 8.         |
| `m_dup`      |    2 |       0 | `00` different, `01` same as next, `10` same as previous.      |
| `m_mcount`   |    8 |     255 | Message counter (0-255).                                       |
| `nid_c`      |   10 |       0 | Region code (high 7 = region, low 3 = sub-region).             |
| `nid_bg`     |   14 |       0 | Balise identity (high 6 = station, low 8 = balise number).     |
| `q_link`     |    1 |   false | `1` = balise group is linked.                                  |

The values above are raw coded integers; the offset and split semantics stay
with the caller, exactly as in the standard tables.

## 4. Packet 41 — 等级转换信息包 (level transition order)

Every packet object requires `"packet": 41` plus the settable fields below;
the full wire layout, including the fixed and auto-computed fields the
encoder writes, in transmission order:

| Field          | Bits | Value / meaning                                |
| -------------- | ---: | ---------------------------------------------- |
| `nid_packet`   |    8 | fixed `0010 1001` (= 41), written by the encoder |
| `q_dir`        |    2 | 验证方向 (see enums below)                     |
| `l_packet`     |   13 | 信息包位数: bits following this field, auto-computed |
| `q_scale`      |    2 | 距离/长度的分辨率 (see enums below)            |
| `d_leveltr`    |   15 | 到等级转换点的距离                             |
| `m_leveltr`    |    3 | 转换的列控等级                                 |
| `nid_stm`      |    8 | 转换的非 ETCS 等级, only while `m_leveltr == 1` (STM) |
| `l_ackleveltr` |   15 | 等级转换点外方确认区段长度                     |
| `n_iter`       |    5 | 包含等级转换点的数量, auto = `len(transitions)` |
| `transitions`  |    N | repeated items (max 31)                        |

Each `transitions` item: `m_leveltr` (3), `nid_stm` (8, only when that
item's `m_leveltr == 1`; ignored otherwise), `l_ackleveltr` (15,
等级转换点外方确认区段长度).

Enum values from the table (informational — the encoder accepts raw coded
integers and does not restrict enum membership):

- `q_dir`: `00` reverse valid, `01` forward valid, `10` both, `11` reserved.
- `q_scale`: `00` = 10 cm, `01` = 1 m, `10` = 10 m.
- `m_leveltr`: `000` ETCS-0, `001` STM, `010` ETCS-1, `011` ETCS-2
  (CTCS-3), `100` ETCS-3 (CTCS-4).
- `nid_stm`: `0000 0001` CTCS-0, `0000 0010` CTCS-1, `0000 0011` CTCS-2,
  `0001 0000` reserved.

## 5. Validation rules

- Unknown fields at any level (`telegram`, packet, item) are rejected —
  typos must not silently drop data.
- Integer fields must be JSON integers (not booleans, not strings) in
  `[0, 2^bits - 1]`.
- Missing required fields, `packet` numbers other than 41, oversized
  repeat lists, and content that exceeds the frame are rejected.

## 6. Specification provenance and open checks

- Frame layout (§2, §3): the CTCS transponder telegram table supplied by
  the project owner (50-bit header, 772-bit user packet area, 8-bit
  `1111 1111` information-end field).
- Packet 41 (framing, widths, enums) is the CTCS level transition order
  table supplied by the project owner; it uses an 8-bit `NID_PACKET` and
  `NID_STM`, unlike the 10-bit `NID_PACKET`/`NID_NTC` of ETCS Baseline 3.
- Open convention check: `L_PACKET` is encoded as the number of bits
  following the `L_PACKET` field. If your ATP expects it to count the whole
  packet (including `NID_PACKET`/`L_PACKET`), adjust `encode_packet()` in
  `packets.py`, this document, and the golden vectors in
  `tests/test_telegram_codec.py` together.
