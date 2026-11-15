# Hardware

Genesis runs on what you already own. `genesis doctor` reads the machine and recommends a tier.

## Three shapes that work

| Shape | Example | Tier | Notes |
|---|---|---|---|
| Pocket | Raspberry Pi 5 with 8 GB or more, NVMe drive, battery hat | seed, or ark with a 1 TB drive | AI is limited to the small models. Everything else runs well |
| Laptop | Any machine with 16 GB of RAM and an external SSD | ark | The vault lives on the external drive and moves between machines |
| Base station | A desktop or server with a GPU that has 12 GB or more | ark or eden | Runs the larger models and serves a whole household |

## Storage

* Use an SSD. Wikipedia alone is a 127 GB file and spinning disks make it slow to search.
* Format the vault drive as ext4 or exFAT. FAT32 cannot hold files over 4 GB.
* Keep a second sealed copy somewhere else. `genesis seal` then copy the drive.

## Power budget

Work out how long a battery lasts before you need it.

```
hours = battery watt hours × 0.85 ÷ average watts
```

Typical draw: a Raspberry Pi 5 with an NVMe drive idles near 5 W and peaks near 12 W. A small PC idles at 10 to 25 W. A desktop with a GPU running a model draws 150 to 350 W. A 100 Wh power bank runs the Pi for roughly 10 hours and the desktop for about 20 minutes.

For solar, size the panel at about four times the average draw to cover clouds and night: a 5 W Pi wants a 20 W panel and a battery.

## A field build

A proven layout, after the [Doomsday Cyberdeck](https://github.com/EzioDEVio/Doomsday-Cyberdeck) project:

* hard case with a foam or printed panel
* Raspberry Pi 5 with 16 GB of RAM, NVMe drive on a PCIe board
* 10 inch touchscreen and a small keyboard
* battery hat with 18650 cells, charging by USB C and by barrel jack so a vehicle or solar controller can feed it
* an RTL-SDR receiver and a LoRa radio on USB
* panel mount ports so the case stays closed in rain and dust

## Hotspot

With no other network, let phones reach the box directly:

```sh
sudo nmcli device wifi hotspot ssid Genesis password "choose a long passphrase"
```

Then open `http://10.42.0.1:8080` from any phone that joins. The Library page serves the Android apps for side loading.

## Radios

| Device | Works with |
|---|---|
| RTL-SDR Blog V3 or V4 | `radio`, `adsb`, `ais` profiles |
| HackRF, Airspy, SDRplay | OpenWebRX+ and the desktop SDR programs |
| Heltec, RAK, LilyGo, Seeed LoRa boards | Meshtastic and MeshCore firmware in the radio pack |
| Common handhelds | CHIRP for programming |
