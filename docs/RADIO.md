# Radio

When the networks are down, radio is what is left. Genesis carries three things: software that runs on the box, software and firmware to carry to other machines and radios, and reference data.

**Listening is open to everyone in most places. Transmitting on amateur or GMRS frequencies needs a license. Know your local rules before you key up.**

## What a cheap receiver gets you

A USB RTL-SDR receiver costs about as much as a meal and turns the box into a wide band receiver. `genesis doctor` tells you which receivers it sees.

| Profile | Service | What you hear or see |
|---|---|---|
| `genesis up radio` | OpenWebRX+ on port 8073 | Tune from any browser on the network: broadcast, air band, marine, weather radio, amateur voice and digital modes |
| `genesis up adsb` | readsb and tar1090 on port 8098 | Aircraft positions from ADS-B on 1090 MHz |
| `genesis up ais` | AIS-catcher on port 8099 | Ship positions from AIS on 162 MHz |

One receiver serves one profile at a time. Add a second receiver to run two.

First run of the receiver needs an admin user:

```sh
docker exec -it genesis-openwebrx-1 openwebrx admin adduser admin
```

For the aircraft tracker set your position so ranges are right: `export GENESIS_LAT=43.7384 GENESIS_LON=7.4246` before `genesis up adsb`.

## Mesh: text with no infrastructure

| Item | Use |
|---|---|
| Meshtastic firmware and Android app | Encrypted text over LoRa radios that cost very little. `genesis up comms` serves the web client on port 8097 |
| MeshCore firmware | A second LoRa mesh with repeaters and room servers |
| Reticulum, Nomad Network, Sideband, MeshChat, RNode firmware | An encrypted networking stack that runs over LoRa, packet radio, serial links or WiFi |
| bitchat, Briar | Phone to phone messaging over Bluetooth and local WiFi |
| Rattlegram | Send short text through any voice radio by holding a phone to the microphone |

Browsers allow serial and Bluetooth access to radios only from secure pages. From another device, connect the web client to a node over the node's own network address, or use the phone app.

## Amateur radio

`genesis fetch --pack radio --tier ark` brings the desktop programs for whatever platform you chose: SDR++, SDRangel, Gqrx, SatDump, SDRTrunk, WSJT-X, JS8Call, Fldigi, FreeDV, Pat for Winlink and Dire Wolf. CHIRP, for programming handhelds, comes as source code: its publisher hands the installers only to a browser, so get one from [chirpmyradio.com](https://chirpmyradio.com) while you still have a connection.

Genesis is not a radio operating system. For a field station with rig control and everything configured, install [EmComm Tools OS](https://github.com/thetechprepper/emcomm-tools-os-community) and point it at the same vault.

## Reference data

| File in `<vault>/radio/data/` | Use |
|---|---|
| `l_amat.zip` | The FCC amateur license database: look up any US callsign offline |
| `sked-b26.csv` | EiBi shortwave schedule: what broadcasts where and when |
| `tle_amateur.txt`, `tle_weather.txt`, `tle_gnss.txt` | Satellite orbits for pass prediction. They age: refresh monthly while you can |

The dashboard's Radio page has the working frequency tables (distress and calling, weather radio, FRS and GMRS, MURS, marine, mesh), the phonetic alphabet, Morse code and distress signals. It prints cleanly.

## Local network comms

`genesis up comms` also starts text chat (an IRC server with a web client on port 8095) and a Mumble voice server on port 64738 for everyone on the hotspot. Create the first chat user with:

```sh
docker exec -it genesis-chat-1 thelounge add yourname
```
