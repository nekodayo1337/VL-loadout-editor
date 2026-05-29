"""Browser-based Loadout Editor: a local HTTP server serving a single-page app."""

import json
import os
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
import urllib3

from src import ui

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ITEMTYPE = {
    "skin_level": "e7c63390-eda7-46e0-bb7a-a6abdacd2433",
    "skin_chroma": "3ad1b2b2-acdb-4524-852f-954a76ddae0a",
    "spray": "d5f120f8-ff8c-4aac-92ea-f2b5acbe9475",
    "card": "3f296c07-64c3-494c-923b-fe692a4fa1bd",
    "title": "de7caa6b-adf7-4588-bbd1-143831e786c6",
    "flex": "03a572de-4234-31ed-d344-ababa488f981",
    "buddy": "dd3bf334-87f3-40bd-b043-682a57a8dc3a",
}

EXPR_TYPE_SPRAY = ITEMTYPE["spray"]
EXPR_TYPE_FLEX = ITEMTYPE["flex"]

CURRENCY = {
    "vp": "85ad13f7-3d1b-5128-9eb2-7cd8ee0b5741",
    "rp": "e59aa87c-4cbf-517a-5983-6e81511be9b7",
    "kc": "85ca954a-41f2-ce94-9b45-8ca3dd39a00d",
}

VALAPI = "https://valorant-api.com/v1/"
REPO = "nekodayo1337/VL-loadout-editor"

CATEGORY_ORDER = ["Sidearm", "SMG", "Shotgun", "Rifle", "Sniper", "Heavy", "Melee"]
CATEGORY_LABELS = {
    "Sidearm": "Sidearms", "SMG": "SMGs", "Shotgun": "Shotguns", "Rifle": "Rifles",
    "Sniper": "Snipers", "Heavy": "Heavies", "Melee": "Melee",
}


def _hexcolor(c):
    if not c:
        return None
    c = str(c).strip().lstrip("#")
    return "#" + c[:6] if len(c) >= 6 else None


class LoadoutGui:
    def __init__(self, log, requests_client, version="alpha"):
        self.log = log
        self.req = requests_client
        self.version = version
        self.loadout = None
        self.gun_by_id = {}
        self.skin_by_id = {}
        self.level_to_skin = {}
        self.buddy_by_uuid = {}
        self._map_path_to_uuid = {}
        self._item_index = {}
        self._bundle_by_uuid = {}
        self.payload = None

    def _loadout_url(self):
        return f"{self.req.pd_url}/personalization/v3/players/{self.req.puuid}/playerloadout"

    def _entitlements(self, item_type):
        url = f"{self.req.pd_url}/store/v1/entitlements/{self.req.puuid}/{item_type}"
        r = requests.get(url, headers=self.req.get_headers(), verify=False, timeout=10)
        r.raise_for_status()
        return {e["ItemID"].lower() for e in r.json().get("Entitlements", [])}

    def _entitlements_safe(self, item_type, label):
        try:
            return self._entitlements(item_type)
        except Exception as e:
            self.log(f"{label} entitlement fetch failed: {e}")
            return set()

    def _entitlements_full(self, item_type):
        url = f"{self.req.pd_url}/store/v1/entitlements/{self.req.puuid}/{item_type}"
        r = requests.get(url, headers=self.req.get_headers(), verify=False, timeout=10)
        r.raise_for_status()
        return r.json().get("Entitlements", [])

    def _valapi(self, path):
        return requests.get(VALAPI + path, timeout=20).json()["data"]

    def _put(self):
        return requests.put(
            self._loadout_url(), headers=self.req.get_headers(),
            json=self.loadout, verify=False, timeout=10,
        )

    def build_data(self):
        weapons = self._valapi("weapons")
        sprays_meta = self._valapi("sprays")
        cards_meta = self._valapi("playercards")
        titles_meta = self._valapi("playertitles")
        try:
            tiers_meta = self._valapi("contenttiers")
        except Exception as e:
            self.log(f"content tiers fetch failed: {e}")
            tiers_meta = []
        try:
            flex_meta = self._valapi("flex")
        except Exception as e:
            self.log(f"flex meta fetch failed: {e}")
            flex_meta = []

        owned_levels = self._entitlements_safe(ITEMTYPE["skin_level"], "skin level")
        owned_chromas = self._entitlements_safe(ITEMTYPE["skin_chroma"], "chroma")
        owned_sprays = self._entitlements_safe(ITEMTYPE["spray"], "spray")
        owned_cards = self._entitlements_safe(ITEMTYPE["card"], "card")
        owned_titles = self._entitlements_safe(ITEMTYPE["title"], "title")
        owned_flex = self._entitlements_safe(ITEMTYPE["flex"], "flex")
        favorites = self._favorites()

        r = requests.get(self._loadout_url(), headers=self.req.get_headers(), verify=False, timeout=10)
        r.raise_for_status()
        self.loadout = r.json()
        self.log(f"loadout top-level keys: {list(self.loadout.keys())}")
        self.gun_by_id = {g.get("ID", "").lower(): g for g in self.loadout.get("Guns", [])}
        identity = self.loadout.get("Identity", {})

        try:
            maps_meta = self._valapi("maps")
        except Exception as e:
            self.log(f"maps fetch failed: {e}"); maps_meta = []
        try:
            agents_meta = self._valapi("agents")
        except Exception as e:
            self.log(f"agents fetch failed: {e}"); agents_meta = []
        self._map_path_to_uuid = {
            (m.get("mapUrl") or "").lower(): m["uuid"].lower()
            for m in maps_meta if m.get("mapUrl")
        }
        maps_list = sorted(
            [{"uuid": m["uuid"].lower(), "name": m["displayName"],
              "icon": m.get("listViewIcon") or m.get("displayIcon")}
             for m in maps_meta if m.get("displayName") and m.get("mapUrl")],
            key=lambda m: m["name"],
        )
        agents_list = sorted(
            [{"uuid": a["uuid"].lower(), "name": a["displayName"], "icon": a.get("displayIcon")}
             for a in agents_meta if a.get("isPlayableCharacter")],
            key=lambda a: a["name"],
        )

        tier_by = {t["uuid"].lower(): t for t in tiers_meta}
        tiers_list = sorted(
            [{"uuid": t["uuid"], "name": t["displayName"], "rank": t.get("rank", 0),
              "icon": t.get("displayIcon"), "color": _hexcolor(t.get("highlightColor"))}
             for t in tiers_meta],
            key=lambda t: t["rank"],
        )

        buddies_meta = self._valapi("buddies")
        buddies_payload = self._build_buddies(buddies_meta)
        weapons_payload = self._build_weapons(weapons, owned_levels, owned_chromas, tier_by, favorites)
        try:
            bundles_meta = self._valapi("bundles")
        except Exception as e:
            self.log(f"bundles fetch failed: {e}"); bundles_meta = []
        self._bundle_by_uuid = {
            b["uuid"].lower(): {"name": b["displayName"], "icon": b.get("displayIcon")}
            for b in bundles_meta
        }
        self._build_item_index(sprays_meta, buddies_meta, cards_meta, titles_meta, flex_meta)
        expressions_payload = self._build_expressions(sprays_meta, flex_meta)
        sprays_payload = self._build_sprays(sprays_meta, owned_sprays)
        flex_payload = self._build_flex(flex_meta, owned_flex)
        cards_payload = self._build_cards(cards_meta, owned_cards, identity.get("PlayerCardID"))
        titles_payload = self._build_titles(titles_meta, owned_titles, identity.get("PlayerTitleID"))

        self.payload = {
            "weapons": weapons_payload,
            "tiers": tiers_list,
            "expressions": expressions_payload,
            "sprays": sprays_payload,
            "flex": flex_payload,
            "cards": cards_payload,
            "titles": titles_payload,
            "buddies": buddies_payload,
            "wallet": self._wallet(),
            "store": self._build_store(),
            "maps": maps_list,
            "agents": agents_list,
            "update": self._check_update(),
        }
        return self.payload

    def _check_update(self):
        try:
            r = requests.get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=8)
            if r.ok:
                j = r.json()
                tag = j.get("tag_name") or ""
                url = j.get("html_url") or f"https://github.com/{REPO}/releases"
                avail = bool(tag) and tag.lstrip("v") != str(self.version).lstrip("v")
                return {"available": avail, "latest": tag, "url": url}
        except Exception as e:
            self.log(f"update check failed: {e}")
        return {"available": False}

    def _gamestate(self):
        glz = self.req.glz_url
        puuid = self.req.puuid
        h = self.req.get_headers()

        def jget(url):
            try:
                r = requests.get(url, headers=h, verify=False, timeout=5)
                return r.json() if r.ok else None
            except Exception:
                return None

        def self_agent(players):
            for p in players or []:
                if p.get("Subject") == puuid:
                    return (p.get("CharacterID") or "").lower() or None
            return None

        cg = jget(f"{glz}/core-game/v1/players/{puuid}")
        if cg and cg.get("MatchID"):
            m = jget(f"{glz}/core-game/v1/matches/{cg['MatchID']}") or {}
            return {"state": "INGAME",
                    "map": self._map_path_to_uuid.get((m.get("MapID") or "").lower()),
                    "agent": self_agent(m.get("Players"))}
        pg = jget(f"{glz}/pregame/v1/players/{puuid}")
        if pg and pg.get("MatchID"):
            m = jget(f"{glz}/pregame/v1/matches/{pg['MatchID']}") or {}
            return {"state": "PREGAME",
                    "map": self._map_path_to_uuid.get((m.get("MapID") or "").lower()),
                    "agent": self_agent((m.get("AllyTeam") or {}).get("Players"))}
        return {"state": "MENUS", "map": None, "agent": None}

    def apply_preset(self, preset):
        if not isinstance(preset, dict):
            return {"ok": False, "error": "bad preset"}
        try:
            g = requests.get(self._loadout_url(), headers=self.req.get_headers(), verify=False, timeout=10)
            fresh = g.json() if g.ok else (self.loadout or {})
        except Exception:
            fresh = self.loadout or {}
        new = dict(preset)
        new["Subject"] = fresh.get("Subject", self.req.puuid)
        new["Version"] = fresh.get("Version")

        def m():
            self.loadout = new
        return self._apply(m)

    def _presets_path(self):
        base = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), "LoadoutEditor")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "presets.json")

    def load_presets(self):
        try:
            with open(self._presets_path(), encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        data.setdefault("presets", [])
        data.setdefault("override", "off")
        return data

    def save_presets(self, data):
        if not isinstance(data, dict):
            return {"ok": False, "error": "bad data"}
        out = {"presets": data.get("presets", []), "override": data.get("override", "off")}
        try:
            with open(self._presets_path(), "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2)
            return {"ok": True}
        except Exception as e:
            self.log(f"save_presets failed: {e}")
            return {"ok": False, "error": str(e)}

    def _build_item_index(self, sprays, buddies, cards, titles, flex):
        idx = dict(self.level_to_skin)
        for s in sprays:
            idx[s["uuid"].lower()] = {"name": s["displayName"],
                                      "icon": s.get("displayIcon") or s.get("fullTransparentIcon")}
        for b in buddies:
            entry = {"name": b["displayName"], "icon": b.get("displayIcon")}
            idx[b["uuid"].lower()] = entry
            for lv in (b.get("levels") or []):
                idx[lv["uuid"].lower()] = entry
        for c in cards:
            idx[c["uuid"].lower()] = {"name": c["displayName"],
                                      "icon": c.get("displayIcon") or c.get("smallArt")}
        for t in titles:
            idx[t["uuid"].lower()] = {"name": t.get("titleText") or t["displayName"], "icon": None}
        for f in flex:
            idx[f["uuid"].lower()] = {"name": f["displayName"], "icon": f.get("displayIcon")}
        self._item_index = idx

    def _resolve(self, item_id):
        return self._item_index.get((item_id or "").lower())

    def _storefront(self):
        h = self.req.get_headers()
        try:
            r = requests.post(f"{self.req.pd_url}/store/v3/storefront/{self.req.puuid}",
                              headers=h, json={}, verify=False, timeout=10)
            if r.ok:
                return r.json()
            self.log(f"storefront v3 POST {r.status_code}")
        except Exception as e:
            self.log(f"storefront v3 error: {e}")
        try:
            r = requests.get(f"{self.req.pd_url}/store/v2/storefront/{self.req.puuid}",
                             headers=h, verify=False, timeout=10)
            if r.ok:
                return r.json()
            self.log(f"storefront v2 GET {r.status_code}")
        except Exception as e:
            self.log(f"storefront v2 error: {e}")
        return None

    def _build_store(self):
        sf = self._storefront()
        if not sf:
            return {"available": False}
        vp = CURRENCY["vp"]
        spl = sf.get("SkinsPanelLayout", {}) or {}
        daily = []
        for off in spl.get("SingleItemStoreOffers", []) or []:
            rew = (off.get("Rewards") or [{}])[0]
            sk = self.level_to_skin.get((rew.get("ItemID") or "").lower())
            daily.append({
                "name": sk["name"] if sk else "Unknown",
                "icon": sk["icon"] if sk else None,
                "tierColor": sk.get("tierColor") if sk else None,
                "tierIcon": sk.get("tierIcon") if sk else None,
                "cost": (off.get("Cost") or {}).get(vp),
            })
        night = []
        bonus = sf.get("BonusStore", {}) or {}
        for off in bonus.get("BonusStoreOffers", []) or []:
            base = off.get("Offer", {}) or {}
            rew = (base.get("Rewards") or [{}])[0]
            sk = self.level_to_skin.get((rew.get("ItemID") or "").lower())
            disc = off.get("DiscountCosts", {}) or {}
            night.append({
                "name": sk["name"] if sk else "Unknown",
                "icon": sk["icon"] if sk else None,
                "tierColor": sk.get("tierColor") if sk else None,
                "cost": (base.get("Cost") or {}).get(vp),
                "discountCost": disc.get(vp),
                "percent": off.get("DiscountPercent"),
            })
        fb = sf.get("FeaturedBundle", {}) or {}
        raw_bundles = fb.get("Bundles") or ([fb.get("Bundle")] if fb.get("Bundle") else [])
        bundles = []
        for b in raw_bundles:
            if not b:
                continue
            meta = self._bundle_by_uuid.get((b.get("DataAssetID") or "").lower(), {})
            items = []
            for it in (b.get("Items") or []):
                info = self._resolve((it.get("Item", {}) or {}).get("ItemID"))
                if info:
                    items.append({"name": info["name"], "icon": info.get("icon")})
            bundles.append({
                "name": meta.get("name") or "Bundle",
                "icon": meta.get("icon"),
                "cost": (b.get("TotalDiscountedCost") or b.get("TotalBaseCost") or {}).get(vp),
                "items": items[:16],
                "seconds": b.get("DurationRemainingInSeconds") or fb.get("BundleRemainingDurationInSeconds", 0),
            })

        kc = CURRENCY["kc"]
        acc = sf.get("AccessoryStore", {}) or {}
        accessory = []
        for off in acc.get("AccessoryStoreOffers", []) or []:
            base = off.get("Offer", {}) or {}
            info = self._resolve(((base.get("Rewards") or [{}])[0]).get("ItemID"))
            accessory.append({
                "name": info["name"] if info else "Item",
                "icon": info.get("icon") if info else None,
                "cost": (base.get("Cost") or {}).get(kc),
            })

        return {
            "available": True,
            "daily": daily,
            "remainingSeconds": spl.get("SingleItemOffersRemainingDurationInSeconds", 0),
            "night": night,
            "nightSeconds": bonus.get("BonusStoreRemainingDurationInSeconds", 0),
            "bundles": bundles,
            "accessory": accessory,
            "accessorySeconds": acc.get("AccessoryStoreRemainingDurationInSeconds", 0),
        }

    def _build_buddies(self, buddies_meta):
        by_level = {}
        for b in buddies_meta:
            for lv in (b.get("levels") or []):
                by_level[lv["uuid"].lower()] = {
                    "uuid": b["uuid"], "name": b["displayName"],
                    "icon": b.get("displayIcon"), "levelId": lv["uuid"],
                }
        try:
            ents = self._entitlements_full(ITEMTYPE["buddy"])
        except Exception as e:
            self.log(f"buddy entitlement fetch failed: {e}")
            ents = []
        owned = {}
        for e in ents:
            info = by_level.get((e.get("ItemID") or "").lower())
            inst = e.get("InstanceID")
            if not info or not inst:
                continue
            key = info["uuid"].lower()
            if key not in owned:
                owned[key] = {**info, "instance": inst}
        self.buddy_by_uuid = {k: v for k, v in owned.items()}
        items = sorted(owned.values(), key=lambda b: b["name"])
        self.log(f"owned buddies: {len(items)}")
        return {"items": items}

    def _wallet(self):
        try:
            url = f"{self.req.pd_url}/store/v1/wallet/{self.req.puuid}"
            r = requests.get(url, headers=self.req.get_headers(), verify=False, timeout=10)
            if not r.ok:
                self.log(f"wallet fetch HTTP {r.status_code}")
                return {}
            bal = r.json().get("Balances", {}) or {}
            return {k: bal.get(uuid, 0) for k, uuid in CURRENCY.items()}
        except Exception as e:
            self.log(f"wallet fetch failed: {e}")
            return {}

    def _favorites(self):
        try:
            url = f"{self.req.pd_url}/favorites/v1/players/{self.req.puuid}/favorites"
            r = requests.get(url, headers=self.req.get_headers(), verify=False, timeout=10)
            if not r.ok:
                self.log(f"favorites fetch HTTP {r.status_code}")
                return set()
            fav = r.json().get("FavoritedContent", {}) or {}
            ids = set()
            for k, v in fav.items():
                ids.add(k.lower())
                if isinstance(v, dict) and v.get("ItemID"):
                    ids.add(v["ItemID"].lower())
            self.log(f"favorites: {len(ids)} ids")
            return ids
        except Exception as e:
            self.log(f"favorites fetch failed: {e}")
            return set()

    def _build_expressions(self, sprays_meta, flex_meta):
        spray_by = {s["uuid"].lower(): s for s in sprays_meta}
        flex_by = {f["uuid"].lower(): f for f in flex_meta}
        active = self.loadout.get("ActiveExpressions") or []
        slots = []
        for i, e in enumerate(active):
            tid = (e.get("TypeID") or "").lower()
            aid = e.get("AssetID") or ""
            kind, icon, name = "empty", None, "Empty"
            if tid == EXPR_TYPE_SPRAY.lower():
                kind = "spray"
                m = spray_by.get(aid.lower())
                if m:
                    icon = m.get("fullTransparentIcon") or m.get("displayIcon")
                    name = m["displayName"]
            elif tid == EXPR_TYPE_FLEX.lower():
                kind = "flex"
                m = flex_by.get(aid.lower())
                if m:
                    icon = m.get("displayIcon")
                    name = m["displayName"]
            slots.append({"index": i, "kind": kind, "assetId": aid, "icon": icon, "name": name})
        self.log(f"ActiveExpressions slots: {len(slots)}")
        return {"slots": slots, "available": len(slots) > 0}

    def _build_weapons(self, weapons, owned_levels, owned_chromas, tier_by=None, favorites=None):
        tier_by = tier_by or {}
        favorites = favorites or set()
        out = []
        for w in weapons:
            wid = w["uuid"].lower()
            default_skin = (w.get("defaultSkinUuid") or "").lower()
            category = (w.get("category") or "").split("::")[-1]
            skins_payload = []
            for skin in w.get("skins", []):
                name = skin["displayName"]
                if name.lower().startswith("random"):
                    continue
                levels = skin.get("levels") or []
                chromas = skin.get("chromas") or []
                if not levels:
                    continue
                level_uuids = [lv["uuid"] for lv in levels]
                base_icon = (
                    skin.get("displayIcon")
                    or (chromas[0].get("fullRender") if chromas else None)
                    or (levels[-1].get("displayIcon") if levels else None)
                )
                tier_uuid = (skin.get("contentTierUuid") or "").lower()
                tier = tier_by.get(tier_uuid)
                tier_color = _hexcolor(tier.get("highlightColor")) if tier else None
                tier_icon = tier.get("displayIcon") if tier else None
                for lv in levels:
                    self.level_to_skin[lv["uuid"].lower()] = {
                        "name": name, "icon": base_icon,
                        "tierColor": tier_color, "tierIcon": tier_icon,
                    }
                self.skin_by_id[skin["uuid"].lower()] = {"name": name, "icon": base_icon}

                owned_level_uuids = [u for u in level_uuids if u.lower() in owned_levels]
                is_default = skin["uuid"].lower() == default_skin
                if not owned_level_uuids and not is_default:
                    continue
                level_payload = [
                    {
                        "uuid": lv["uuid"],
                        "name": lv.get("displayName") or f"Level {i + 1}",
                        "icon": lv.get("displayIcon") or base_icon,
                        "owned": lv["uuid"].lower() in owned_levels or (is_default and i == 0),
                    }
                    for i, lv in enumerate(levels)
                ]
                chroma_payload = [
                    {
                        "uuid": ch["uuid"],
                        "name": ch.get("displayName") or f"Variant {i + 1}",
                        "swatch": ch.get("swatch"),
                        "render": ch.get("fullRender") or base_icon,
                        "owned": i == 0 or ch["uuid"].lower() in owned_chromas,
                    }
                    for i, ch in enumerate(chromas)
                ]
                all_ids = {skin["uuid"].lower(), *[u.lower() for u in level_uuids],
                           *[c["uuid"].lower() for c in chromas]}
                skins_payload.append(
                    {"uuid": skin["uuid"], "name": name, "icon": base_icon,
                     "levels": level_payload, "chromas": chroma_payload,
                     "tier": tier_uuid or None,
                     "tierName": tier["displayName"] if tier else None,
                     "tierRank": tier.get("rank", -1) if tier else -1,
                     "tierIcon": tier.get("displayIcon") if tier else None,
                     "tierColor": _hexcolor(tier.get("highlightColor")) if tier else None,
                     "favorite": bool(all_ids & favorites)}
                )
            skins_payload.sort(key=lambda s: s["name"])
            gun = self.gun_by_id.get(wid)
            current = {
                "skinId": gun.get("SkinID") if gun else None,
                "levelId": gun.get("SkinLevelID") if gun else None,
                "chromaId": gun.get("ChromaID") if gun else None,
                "charmId": gun.get("CharmID") if gun else None,
            }
            cs = self.skin_by_id.get((current["skinId"] or "").lower())
            out.append(
                {"uuid": wid, "name": w["displayName"], "category": category,
                 "categoryLabel": CATEGORY_LABELS.get(category, category or "Other"),
                 "categoryOrder": CATEGORY_ORDER.index(category) if category in CATEGORY_ORDER else 99,
                 "current": current, "currentSkinIcon": cs["icon"] if cs else None,
                 "currentSkinName": cs["name"] if cs else "Default", "skins": skins_payload}
            )
        out.sort(key=lambda w: (w["categoryOrder"], w["name"]))
        return out

    def _build_sprays(self, sprays_meta, owned):
        equipped = {(e.get("AssetID") or "").lower() for e in (self.loadout.get("ActiveExpressions") or [])}
        items = []
        for s in sprays_meta:
            if s.get("isNullSpray"):
                continue
            sid = s["uuid"].lower()
            if sid in owned or sid in equipped:
                items.append(
                    {"uuid": s["uuid"], "name": s["displayName"],
                     "icon": s.get("displayIcon") or s.get("fullTransparentIcon"),
                     "animated": bool(s.get("animationGif") or s.get("animationPng"))}
                )
        items.sort(key=lambda s: s["name"])
        return {"items": items}

    def _build_flex(self, flex_meta, owned):
        equipped = {(e.get("AssetID") or "").lower() for e in (self.loadout.get("ActiveExpressions") or [])}
        items = []
        for f in flex_meta:
            fid = f["uuid"].lower()
            if not owned or fid in owned or fid in equipped:
                items.append({"uuid": f["uuid"], "name": f["displayName"], "icon": f.get("displayIcon")})
        items.sort(key=lambda f: f["name"])
        return {"items": items, "editable": bool(self.loadout.get("ActiveExpressions"))}

    def _build_cards(self, cards_meta, owned, current):
        items = []
        cur_l = (current or "").lower()
        for c in cards_meta:
            cid = c["uuid"].lower()
            if cid in owned or cid == cur_l:
                items.append(
                    {"uuid": c["uuid"], "name": c["displayName"],
                     "icon": c.get("displayIcon") or c.get("smallArt"),
                     "large": c.get("largeArt") or c.get("displayIcon") or c.get("smallArt")}
                )
        items.sort(key=lambda c: c["name"])
        return {"items": items, "current": current}

    def _build_titles(self, titles_meta, owned, current):
        items = [{"uuid": "", "text": "(No Title)", "name": "None"}]
        cur_l = (current or "").lower()
        for t in titles_meta:
            tid = t["uuid"].lower()
            text = t.get("titleText") or ""
            if not text:
                continue
            if tid in owned or tid == cur_l:
                items.append({"uuid": t["uuid"], "text": text, "name": t["displayName"]})
        items.sort(key=lambda t: (t["uuid"] == "" and "" or t["text"].lower()))
        return {"items": items, "current": current}

    def _apply(self, mutate):
        snapshot = json.loads(json.dumps(self.loadout))
        old_version = self.loadout.get("Version")
        mutate()
        try:
            r = self._put()
        except Exception as e:
            self.loadout = snapshot
            self.log(f"put error: {e}")
            return {"ok": False, "status": 0, "error": str(e)}
        if r.status_code == 200:
            persisted = None
            try:
                g = requests.get(self._loadout_url(), headers=self.req.get_headers(), verify=False, timeout=10)
                if g.ok:
                    self.loadout = g.json()
                    self.gun_by_id = {gn.get("ID", "").lower(): gn for gn in self.loadout.get("Guns", [])}
                    new_version = self.loadout.get("Version")
                    persisted = new_version != old_version
                    self.log(f"loadout Version {old_version} -> {new_version} (persisted={persisted})")
            except Exception as e:
                self.log(f"post-put refresh failed: {e}")
            return {"ok": True, "status": 200, "persisted": persisted}
        self.loadout = snapshot
        self.log(f"put rejected {r.status_code}: {r.text[:300]}")
        return {"ok": False, "status": r.status_code}

    def equip(self, req):
        kind = req.get("type", "skin")
        if kind == "skin":
            wid = (req.get("weapon") or "").lower()

            def m():
                gun = self.gun_by_id.get(wid)
                if gun is None:
                    gun = {"ID": req.get("weapon")}
                    self.loadout.setdefault("Guns", []).append(gun)
                    self.gun_by_id[wid] = gun
                gun["SkinID"] = req.get("skinId")
                gun["SkinLevelID"] = req.get("levelId")
                gun["ChromaID"] = req.get("chromaId")
            return self._apply(m)

        if kind == "card":
            return self._apply(lambda: self.loadout.setdefault("Identity", {}).__setitem__("PlayerCardID", req.get("cardId")))

        if kind == "title":
            return self._apply(lambda: self.loadout.setdefault("Identity", {}).__setitem__("PlayerTitleID", req.get("titleId") or ""))

        if kind == "buddy":
            wid = (req.get("weapon") or "").lower()
            buddy_id = req.get("buddyId")

            def m():
                gun = self.gun_by_id.get(wid)
                if gun is None:
                    gun = {"ID": req.get("weapon")}
                    self.loadout.setdefault("Guns", []).append(gun)
                    self.gun_by_id[wid] = gun
                if not buddy_id:
                    for key in ("CharmInstanceID", "CharmID", "CharmLevelID"):
                        gun.pop(key, None)
                    return
                bd = self.buddy_by_uuid.get(buddy_id.lower())
                if not bd:
                    return
                inst = bd["instance"]
                for g in self.loadout.get("Guns", []):
                    if g is not gun and g.get("CharmInstanceID") == inst:
                        for key in ("CharmInstanceID", "CharmID", "CharmLevelID"):
                            g.pop(key, None)
                gun["CharmInstanceID"] = inst
                gun["CharmID"] = bd["uuid"]
                gun["CharmLevelID"] = bd["levelId"]
            return self._apply(m)

        if kind in ("spray", "flex"):
            idx = req.get("slotIndex")
            active = self.loadout.get("ActiveExpressions")
            if not isinstance(active, list) or idx is None or idx < 0 or idx >= len(active):
                return {"ok": False, "status": 0, "error": "expression slot not found"}
            type_id = EXPR_TYPE_SPRAY if kind == "spray" else EXPR_TYPE_FLEX
            asset = req.get("sprayId") if kind == "spray" else req.get("flexId")

            def m():
                self.loadout["ActiveExpressions"][idx] = {"TypeID": type_id, "AssetID": asset}
            return self._apply(m)

        return {"ok": False, "status": 0, "error": "unknown type"}

    def serve(self):
        from rich.console import Console

        console = Console()
        console.print()
        console.print(ui.heading("Loadout Editor — GUI", "opening in your browser"))

        try:
            self.build_data()
        except Exception as e:
            self.log(f"gui build_data failed: {e}")
            ui.notice(console, "Could not load your loadout from Riot.", "warn")
            ui.notice(console, "Make sure the Riot Client is running and logged in.", "info")
            return

        gui = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, body, content_type="application/json"):
                data = body.encode("utf-8") if isinstance(body, str) else body
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    self._send(200, PAGE, "text/html; charset=utf-8")
                elif self.path == "/api/data":
                    self._send(200, json.dumps(gui.payload))
                elif self.path == "/api/refresh":
                    try:
                        gui.build_data()
                        self._send(200, json.dumps(gui.payload))
                    except Exception as e:
                        gui.log(f"refresh failed: {e}")
                        self._send(200, json.dumps({"error": str(e)}))
                elif self.path == "/api/gamestate":
                    self._send(200, json.dumps(gui._gamestate()))
                elif self.path == "/api/loadout-raw":
                    self._send(200, json.dumps(gui.loadout or {}))
                elif self.path == "/api/presets":
                    self._send(200, json.dumps(gui.load_presets()))
                else:
                    self._send(204, b"")

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                try:
                    req = json.loads(self.rfile.read(length) or b"{}")
                except Exception:
                    self._send(400, json.dumps({"ok": False, "error": "bad json"}))
                    return
                if self.path == "/api/equip":
                    self._send(200, json.dumps(gui.equip(req)))
                elif self.path == "/api/apply-preset":
                    self._send(200, json.dumps(gui.apply_preset(req.get("loadout"))))
                elif self.path == "/api/presets":
                    self._send(200, json.dumps(gui.save_presets(req)))
                else:
                    self._send(404, json.dumps({"ok": False}))

        port = None
        for candidate in range(9667, 9717):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", candidate)) != 0:
                    port = candidate
                    break
        if port is None:
            ui.notice(console, "No free local port found for the GUI.", "warn")
            return

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        url = f"http://127.0.0.1:{port}"
        ui.notice(console, f"GUI ready at {url}", "ok")
        ui.notice(console, "Keep this window open. Press Ctrl+C here to close the editor.", "info")
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()
            ui.notice(console, "Loadout editor closed.", "info")


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Loadout Editor</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap">
<style>
  :root{
    --accent:#ff4655; --accent-soft:#ff6470; --accent-glow:rgba(255,70,85,.30);
    --bg:#0b0c10; --panel:#15171e; --panel-2:#1b1e27; --panel-3:#22262f;
    --ink:#f2efe9; --muted:#868f9e; --faint:#2c313d; --line:#20242d; --good:#5fe06b;
    --tc:#2c313d; /* per-card tier colour, set by JS */
    --radius:16px; --ease:cubic-bezier(.4,.1,.2,1);
    --shadow:0 10px 30px rgba(0,0,0,.45);
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{background:
      radial-gradient(1200px 600px at 80% -10%, rgba(255,70,85,.07), transparent 60%),
      radial-gradient(900px 500px at -10% 110%, rgba(95,176,214,.05), transparent 55%),
      var(--bg);
    color:var(--ink);font-family:"Inter",Segoe UI,system-ui,Arial,sans-serif;
    display:flex;flex-direction:column;height:100vh;overflow:hidden;-webkit-font-smoothing:antialiased}
  header{display:flex;align-items:center;gap:14px;padding:13px 22px;position:relative;z-index:5;
    border-bottom:1px solid var(--line);background:linear-gradient(180deg,#13151c,#0b0c10);
    box-shadow:0 1px 0 rgba(255,255,255,.02)}
  header .bar{width:4px;height:26px;background:linear-gradient(var(--accent),var(--accent-soft));
    border-radius:2px;box-shadow:0 0 12px var(--accent-glow)}
  header h1{font-size:15px;margin:0;font-weight:800;letter-spacing:.4px}
  header h1 span{color:var(--muted);font-weight:500}
  .tabs{display:flex;gap:4px;margin-left:20px}
  .tab{padding:8px 15px;border-radius:10px;border:1px solid transparent;background:none;
    color:var(--muted);cursor:pointer;font-size:13px;font-weight:600;transition:all .15s var(--ease)}
  .tab:hover{background:var(--panel);color:var(--ink)}
  .tab.active{background:var(--panel-2);border-color:var(--faint);color:var(--ink);
    box-shadow:inset 0 -2px 0 var(--accent)}
  header .hint{margin-left:auto;color:var(--muted);font-size:12px}
  .refresh{margin-left:16px;padding:8px 15px;border-radius:10px;border:1px solid var(--faint);
    background:var(--panel);color:var(--ink);cursor:pointer;font-size:13px;font-weight:600;
    transition:all .15s var(--ease)}
  .refresh:hover{border-color:var(--accent);color:#fff;background:#241319;box-shadow:0 0 14px var(--accent-glow)}
  .refresh:disabled{opacity:.6;cursor:default}
  .update{margin-left:14px;padding:6px 12px;border-radius:9px;border:1px solid var(--accent);
    background:#241319;color:#fff;font-size:12px;font-weight:700;text-decoration:none;
    box-shadow:0 0 12px var(--accent-glow)}
  .update:hover{background:var(--accent)}
  .lang{margin-left:12px;padding:8px 13px;border-radius:10px;border:1px solid var(--faint);
    background:var(--panel);color:var(--ink);cursor:pointer;font-size:13px;font-weight:600}
  .lang:hover{border-color:var(--accent)}
  .wallet{display:flex;gap:7px;align-items:center;margin-left:16px}
  .cur{font-size:12px;font-weight:700;padding:5px 10px;border-radius:8px;border:1px solid var(--faint);
    background:var(--panel);font-variant-numeric:tabular-nums;white-space:nowrap}
  .cur b{font-weight:800;margin-right:4px;opacity:.7;font-size:10.5px}
  .cur.vp{color:#f4f4f4}.cur.rp{color:#d4af6a}.cur.kc{color:#7fd0c4}
  .view{flex:1;min-height:0;display:none}
  .view.active{display:flex;animation:fade .25s var(--ease)}
  @keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
  .sidebar{width:288px;flex:none;border-right:1px solid var(--line);overflow-y:auto;
    padding:10px 8px;background:#0e1015}
  .cat{color:var(--muted);font-size:10.5px;text-transform:uppercase;letter-spacing:1.4px;
    margin:16px 12px 6px;font-weight:700}
  .wbtn{display:flex;align-items:center;gap:11px;width:100%;padding:9px 11px;border-radius:11px;
    border:1px solid transparent;background:none;color:var(--ink);cursor:pointer;text-align:left;
    position:relative;transition:background .15s var(--ease)}
  .wbtn:hover{background:var(--panel)}
  .wbtn.active{background:var(--panel-2);border-color:var(--faint)}
  .wbtn.active::before{content:"";position:absolute;left:-8px;top:18%;height:64%;width:3px;
    border-radius:0 3px 3px 0;background:var(--accent);box-shadow:0 0 10px var(--accent-glow)}
  .wbtn img{width:66px;height:30px;object-fit:contain;flex:none;filter:drop-shadow(0 1px 3px rgba(0,0,0,.7))}
  .wbtn .wt{min-width:0;flex:1}
  .wbtn .wn{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .wbtn .ws{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .wbtn .cnt{font-size:10px;color:var(--muted);background:var(--panel-3);border-radius:20px;
    padding:2px 7px;flex:none}
  .main{flex:1;display:flex;flex-direction:column;min-width:0}
  .preview{display:flex;align-items:center;gap:24px;padding:22px 28px;border-bottom:1px solid var(--line);
    min-height:150px;position:relative;overflow:hidden;
    background:radial-gradient(620px 300px at 16% 50%, color-mix(in srgb, var(--tc) 28%, transparent), transparent 70%), #0e1015}
  .preview img{height:104px;max-width:46%;object-fit:contain;filter:drop-shadow(0 10px 22px rgba(0,0,0,.7));z-index:1}
  .preview .meta{z-index:1}
  .preview .meta .pw{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:1.6px;font-weight:600}
  .preview .meta .ps{font-size:25px;font-weight:800;margin-top:3px;letter-spacing:.2px}
  .preview .meta .pt{margin-top:8px;display:inline-flex;align-items:center;gap:6px;font-size:11px;
    color:var(--muted);border:1px solid var(--faint);border-radius:20px;padding:3px 10px}
  .preview .meta .pt img{height:14px;width:14px;filter:none}
  .preview .meta .pt.buddy{cursor:pointer;margin-top:6px;margin-left:8px}
  .preview .meta .pt.buddy:hover{border-color:var(--muted)}
  .preview .meta .pt.buddy img{height:18px;width:18px}
  .preview .meta .pt .edit{color:var(--accent);font-weight:600;margin-left:2px}
  .modal{position:fixed;inset:0;background:rgba(4,5,8,.66);display:none;z-index:50;
    align-items:center;justify-content:center;backdrop-filter:blur(2px);animation:fade .2s var(--ease)}
  .modal.show{display:flex}
  .modal-box{width:min(940px,92vw);max-height:84vh;background:var(--panel);border:1px solid var(--faint);
    border-radius:18px;display:flex;flex-direction:column;overflow:hidden;box-shadow:var(--shadow)}
  .modal-head{display:flex;gap:12px;align-items:center;padding:15px 18px;border-bottom:1px solid var(--line)}
  .modal-head b{font-size:15px;white-space:nowrap}
  .modal-head input{flex:1;background:var(--bg);border:1px solid var(--faint);border-radius:9px;
    color:var(--ink);padding:8px 12px;font-size:13px;outline:none}
  .modal-head input:focus{border-color:var(--accent)}
  .modal .grid{overflow-y:auto}
  .variants{margin-left:auto;display:flex;flex-direction:column;gap:12px;align-items:flex-end;z-index:1}
  .vrow{display:flex;gap:8px;align-items:center}
  .vlbl{color:var(--muted);font-size:11px;width:62px;text-align:right;text-transform:uppercase;letter-spacing:1px}
  .chroma{width:30px;height:30px;border-radius:50%;border:2px solid var(--faint);cursor:pointer;
    background-size:cover;background-position:center;padding:0;transition:all .15s var(--ease)}
  .chroma:hover{transform:scale(1.12)}
  .chroma.sel{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-glow)}
  .chroma:disabled{opacity:.22;cursor:not-allowed}
  .lvl{padding:6px 11px;border-radius:9px;border:1px solid var(--faint);background:var(--panel);
    color:var(--ink);font-size:12px;cursor:pointer;font-weight:600;transition:all .15s var(--ease)}
  .lvl.sel{border-color:var(--accent);color:#fff;background:#241319}
  .lvl:disabled{opacity:.3;cursor:not-allowed}
  .grid{flex:1;overflow-y:auto;padding:18px 22px;display:grid;
    grid-template-columns:repeat(auto-fill,minmax(186px,1fr));gap:14px;
    align-content:start;grid-auto-rows:max-content}
  .card{background:linear-gradient(180deg,var(--panel),#13151b);border:1px solid var(--line);
    border-radius:var(--radius);padding:13px 13px 15px;cursor:pointer;
    transition:transform .16s var(--ease),border-color .16s var(--ease),box-shadow .16s var(--ease);
    display:flex;flex-direction:column;gap:8px;position:relative;min-height:0}
  .card::after{content:"";position:absolute;left:0;right:0;bottom:0;height:3px;
    background:var(--tc);opacity:.55;transition:opacity .16s var(--ease)}
  .card:hover{transform:translateY(-3px);border-color:color-mix(in srgb,var(--tc) 60%,var(--faint));
    box-shadow:0 12px 26px rgba(0,0,0,.5)}
  .card:hover::after{opacity:1}
  .card.sel{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent),0 12px 26px var(--accent-glow)}
  .card.sel::after{background:var(--accent);opacity:1}
  .card .imgwrap{height:78px;display:flex;align-items:center;justify-content:center}
  .card img{max-width:100%;max-height:78px;object-fit:contain;
    transition:transform .16s var(--ease);filter:drop-shadow(0 4px 8px rgba(0,0,0,.4))}
  .card:hover img{transform:scale(1.05)}
  .card .nm{font-size:12.5px;font-weight:600;line-height:1.25}
  .card .badge{position:absolute;top:9px;right:9px}
  .card .badge img{width:16px;height:16px;object-fit:contain;filter:drop-shadow(0 1px 2px rgba(0,0,0,.6))}
  .card .fav{position:absolute;top:7px;left:10px;color:var(--accent);font-size:14px;
    text-shadow:0 0 8px var(--accent-glow)}
  .card.spr .imgwrap{height:110px;border-radius:11px;
    background:radial-gradient(circle at 50% 42%,#262b37,#13151b)}
  .card.spr img{max-height:96px;max-width:90%}
  #card-grid{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}
  .card.pc .imgwrap{height:200px;border-radius:11px;background:linear-gradient(180deg,#1b1e27,#13151b);overflow:hidden}
  .card.pc img{max-height:200px;max-width:100%}
  .empty{color:var(--muted);padding:48px;text-align:center;grid-column:1/-1;width:100%}
  .filterbar{display:flex;gap:10px;align-items:center;padding:11px 22px;border-bottom:1px solid var(--line);
    background:#0d0f14;flex-wrap:wrap}
  .filterbar input[type=text]{background:var(--panel);border:1px solid var(--faint);border-radius:9px;
    color:var(--ink);padding:8px 12px;font-size:13px;min-width:220px;outline:none;transition:border-color .15s}
  .filterbar input[type=text]:focus{border-color:var(--accent)}
  .filterbar input::placeholder{color:#5a606e}
  .filterbar select{background:var(--panel);border:1px solid var(--faint);border-radius:9px;color:var(--ink);
    padding:8px 11px;font-size:13px;outline:none;cursor:pointer}
  .chip{padding:7px 13px;border-radius:9px;border:1px solid var(--faint);background:var(--panel);
    color:var(--muted);cursor:pointer;font-size:12px;font-weight:600;transition:all .15s var(--ease)}
  .chip:hover{color:var(--ink)}
  .chip.on{border-color:var(--accent);color:#fff;background:#241319;box-shadow:0 0 10px var(--accent-glow)}
  .filterbar .count{margin-left:auto;color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums}
  .wheelwrap{display:flex;flex-direction:column;min-width:0;flex:1}
  .wheelhead{display:flex;align-items:center;justify-content:center;padding:30px;gap:54px;
    border-bottom:1px solid var(--line);background:radial-gradient(500px 240px at 50% 40%, rgba(255,70,85,.05), transparent), #0e1015}
  .wheel{position:relative;width:264px;height:264px;flex:none}
  .wheel::before{content:"";position:absolute;inset:34px;border-radius:50%;
    border:1px dashed var(--faint);opacity:.7}
  .wheel .hub{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:96px;height:96px;
    border-radius:50%;display:flex;align-items:center;justify-content:center;text-align:center;
    background:radial-gradient(circle,#161922,#0e1015);border:1px solid var(--faint);
    color:var(--muted);font-size:10.5px;text-transform:uppercase;letter-spacing:1.5px;line-height:1.4}
  .slot{position:absolute;width:80px;height:80px;margin:-40px 0 0 -40px;border-radius:50%;
    border:2px solid var(--faint);background:linear-gradient(180deg,var(--panel),#101218);cursor:pointer;
    display:flex;align-items:center;justify-content:center;overflow:hidden;
    transition:transform .15s var(--ease),border-color .15s var(--ease),box-shadow .15s var(--ease)}
  .slot:hover{transform:scale(1.08);border-color:var(--muted)}
  .slot.sel{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-glow),0 0 18px var(--accent-glow)}
  .slot img{max-width:82%;max-height:82%;object-fit:contain}
  .slotcap{color:var(--muted);font-size:13px;max-width:220px}
  .slotcap b{color:var(--ink);display:block;font-size:16px;margin-bottom:5px}
  .store-head{display:flex;align-items:center;padding:20px 24px 4px}
  .store-head .sh-title{font-size:19px;font-weight:800;letter-spacing:.2px}
  .store-head .sh-sub{color:var(--muted);font-size:12px;margin-top:3px;font-variant-numeric:tabular-nums}
  .store-head.night .sh-title{color:var(--gold,#d4af6a)}
  .grid.store-grid{padding-top:10px;grid-template-columns:repeat(auto-fill,minmax(240px,1fr))}
  .card.store{cursor:default}
  .card.store:hover{transform:none;box-shadow:0 12px 26px rgba(0,0,0,.4)}
  .card .scost{display:flex;align-items:center;gap:7px;font-size:13px;font-variant-numeric:tabular-nums;margin-top:2px}
  .card .scost .vp{font-size:10px;font-weight:800;color:var(--muted)}
  .card .scost b{font-weight:800}
  .card .scost .old{text-decoration:line-through;color:var(--muted)}
  .card .scost .off{color:var(--good);font-weight:700;font-size:11px}
  .store-head.bundle .sh-title{color:#ff8fa0}
  .bundle-banner{padding:6px 24px 0}
  .bundle-banner img{width:100%;max-height:220px;object-fit:cover;border-radius:14px;
    border:1px solid var(--line);box-shadow:0 10px 26px rgba(0,0,0,.5)}
  .grid.store-grid.mini{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}
  .presets-list{display:flex;flex-direction:column;gap:10px;padding:16px 22px;overflow-y:auto}
  .preset-row{display:flex;align-items:center;gap:14px;padding:12px 16px;border:1px solid var(--line);
    border-radius:12px;background:linear-gradient(180deg,var(--panel),#13151b)}
  .preset-row .pname{font-size:15px;font-weight:700;min-width:160px}
  .preset-row .psel{color:var(--muted);font-size:12px;display:flex;align-items:center;gap:6px}
  .preset-row .psel select{background:var(--bg);border:1px solid var(--faint);border-radius:8px;
    color:var(--ink);padding:6px 9px;font-size:12px;outline:none;cursor:pointer;max-width:160px}
  .preset-row .chip:last-child{margin-left:0}
  .preset-row .chip{margin-left:auto}
  .preset-row .chip+.chip{margin-left:8px}
  .titlelist{flex:1;overflow-y:auto;padding:18px 22px;display:flex;flex-direction:column;gap:8px}
  .trow{display:flex;align-items:center;gap:12px;padding:13px 17px;border:1px solid var(--line);
    border-radius:12px;background:linear-gradient(180deg,var(--panel),#13151b);cursor:pointer;
    transition:all .14s var(--ease)}
  .trow:hover{background:var(--panel-2);transform:translateX(2px)}
  .trow.sel{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
  .trow .tt{font-size:15px;font-weight:600}
  .trow .tn{margin-left:auto;color:var(--muted);font-size:12px}
  #toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(8px);background:var(--panel-2);
    border:1px solid var(--faint);border-left:4px solid var(--accent);color:var(--ink);padding:12px 20px;
    border-radius:12px;font-size:13px;font-weight:500;opacity:0;transition:all .25s var(--ease);
    pointer-events:none;box-shadow:var(--shadow)}
  #toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
  #toast.ok{border-left-color:var(--good)}
  #toast.err{border-left-color:var(--accent)}
  .loading{padding:60px;text-align:center;color:var(--muted);width:100%}
  .spin{width:30px;height:30px;border-radius:50%;border:3px solid var(--faint);
    border-top-color:var(--accent);margin:0 auto 14px;animation:spin .8s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
  .note{padding:14px 22px;color:var(--muted);font-size:13px}
  ::-webkit-scrollbar{width:10px;height:10px}
  ::-webkit-scrollbar-thumb{background:#2a2f3a;border-radius:6px;border:2px solid transparent;background-clip:padding-box}
  ::-webkit-scrollbar-thumb:hover{background:#39404e;background-clip:padding-box}
  ::-webkit-scrollbar-track{background:transparent}
</style>
</head>
<body>
<header>
  <div class="bar"></div>
  <h1>VALORANT <span>Loadout Editor</span></h1>
  <div class="tabs" id="tabs"></div>
  <div class="hint" id="hint"></div>
  <div class="wallet" id="wallet"></div>
  <a class="update" id="update" target="_blank" rel="noopener" style="display:none"></a>
  <button class="lang" id="lang"></button>
  <button class="refresh" id="refresh" title="Re-fetch your loadout from Riot (use after changing things in-game)">⟳ Refresh</button>
</header>

<div class="view" id="view-weapons">
  <div class="sidebar" id="sidebar"><div class="loading"><div class="spin"></div>Loading…</div></div>
  <div class="main">
    <div class="preview" id="preview"></div>
    <div class="filterbar" id="weapon-filter"></div>
    <div class="grid" id="grid"><div class="loading"><div class="spin"></div>Loading your collection…</div></div>
  </div>
</div>

<div class="view" id="view-sprays">
  <div class="wheelwrap">
    <div class="wheelhead"><div class="wheel" id="spray-wheel"></div>
      <div class="slotcap" id="spray-cap"></div></div>
    <div class="filterbar" id="spray-filter"></div>
    <div class="grid" id="spray-grid"></div>
  </div>
</div>

<div class="view" id="view-flex">
  <div class="wheelwrap">
    <div class="wheelhead"><div class="wheel" id="flex-wheel"></div>
      <div class="slotcap" id="flex-cap"></div></div>
    <div id="flex-note"></div>
    <div class="grid" id="flex-grid"></div>
  </div>
</div>

<div class="view" id="view-card"><div class="wheelwrap">
  <div class="filterbar" id="card-filter"></div>
  <div class="grid" id="card-grid"></div></div></div>
<div class="view" id="view-title"><div class="wheelwrap">
  <div class="filterbar" id="title-filter"></div>
  <div class="titlelist" id="title-list"></div></div></div>
<div class="view" id="view-store"><div class="wheelwrap" id="store-wrap"></div></div>
<div class="view" id="view-presets"><div class="wheelwrap" id="presets-wrap"></div></div>

<div id="buddy-modal" class="modal">
  <div class="modal-box">
    <div class="modal-head"><b id="buddy-title">Gun Buddy</b>
      <input type="text" id="bq">
      <button class="chip" id="buddy-close" onclick="closeBuddyModal()">Close</button></div>
    <div class="grid" id="buddy-grid"></div>
  </div>
</div>

<div id="toast"></div>
<script>
let DATA=null, curWeapon=null, curView='weapons', exprSlot=0, buddyQ='';
const F={weapon:{q:'',tier:'',fav:false,sort:'name'}, spray:{q:'',animated:false}, card:{q:''}, title:{q:''}};
const TABS=[['weapons','Weapons'],['sprays','Sprays'],['flex','Flex'],['card','Player Card'],['title','Title'],['store','Store'],['presets','Presets']];
let LANG=(function(){try{return localStorage.getItem('le_lang')||'en';}catch(e){return 'en';}})();
const I18N={
 en:{update:'Update',refresh:'⟳ Refresh',hint:'1–5 tabs · / search · R random',
  tab_weapons:'Weapons',tab_sprays:'Sprays',tab_flex:'Flex',tab_card:'Player Card',tab_title:'Title',tab_store:'Store',tab_presets:'Presets',
  search_skins:'Search skins…',all_tiers:'All tiers',sort_name:'Sort · Name',sort_rarity:'Sort · Rarity',favorites:'★ Favorites',buddy_btn:'🧸 Buddy',random_btn:'🎲 Random',
  no_skins_weapon:"You don't own any skins for this weapon.",no_match_filter:'No skins match the filters.',variant:'Variant',level:'Level',no_buddy:'No buddy',edit:'edit',
  expr_wheel:'Expressions<br>Wheel',slot:'Slot',pick_spray:'pick a spray below →',pick_flex:'pick a flex below →',search_sprays:'Search sprays…',animated:'▶ Animated',no_sprays:'No sprays match the filters.',no_flex:'No owned flex found.',
  search_cards:'Search cards…',no_cards:'No owned cards found.',no_cards_match:'No cards match the search.',search_titles:'Search titles…',no_titles_match:'No titles match the search.',none_title:'(No Title)',none:'None',
  daily_store:'Daily Store',resets_in:'resets in',night_market:'Night Market',ends_in:'ends in',featured_bundle:'Featured Bundle',accessory_store:'Accessory Store',kingdom_credits:'Kingdom Credits',store_unavailable:'Store unavailable — could not read your storefront.',
  new_preset_name:'New preset name…',save_loadout:'＋ Save current loadout',auto_override:'Auto-override',off:'Off',by_map:'By Map',by_agent:'By Agent',apply_now:'Apply now',
  override_note:'Auto-override is ON. While VALORANT is running, entering agent select / a match auto-applies the preset assigned to that map/agent.',
  no_presets:'No presets yet. Set up your loadout, type a name, and click "Save current loadout".',map_label:'Map',agent_label:'Agent',
  gun_buddy:'Gun Buddy',search_buddies:'Search buddies…',close:'Close',no_owned_buddies:'No owned buddies found.',
  equipped:'Equipped:',spray_set:'Spray set:',flex_set:'Flex set:',buddy_set:'Buddy set:',buddy_removed:'Buddy removed',card_set:'Card set:',title_set:'Title set:',saved_preset:'Saved preset:',applied_preset:'Applied preset',refresh_hint:'(Refresh to see it)',rejected:'Riot rejected the change (HTTP ',req_failed:'Request failed:',no_random:'No skins to randomize',save_failed:'Save failed:'},
 ja:{update:'更新あり',refresh:'⟳ 更新',hint:'1–5: タブ · /: 検索 · R: ランダム',
  tab_weapons:'武器',tab_sprays:'スプレー',tab_flex:'Flex',tab_card:'プレイヤーカード',tab_title:'タイトル',tab_store:'ストア',tab_presets:'プリセット',
  search_skins:'スキンを検索…',all_tiers:'全Tier',sort_name:'並び替え · 名前',sort_rarity:'並び替え · レア度',favorites:'★ お気に入り',buddy_btn:'🧸 バディ',random_btn:'🎲 ランダム',
  no_skins_weapon:'この武器の所持スキンがありません。',no_match_filter:'条件に一致するスキンがありません。',variant:'バリアント',level:'レベル',no_buddy:'バディなし',edit:'変更',
  expr_wheel:'表現<br>ウィホイール',slot:'スロット',pick_spray:'下からスプレーを選択 →',pick_flex:'下からFlexを選択 →',search_sprays:'スプレーを検索…',animated:'▶ アニメ',no_sprays:'条件に一致するスプレーがありません。',no_flex:'所持Flexがありません。',
  search_cards:'カードを検索…',no_cards:'所持カードがありません。',no_cards_match:'検索に一致するカードがありません。',search_titles:'タイトルを検索…',no_titles_match:'検索に一致するタイトルがありません。',none_title:'(タイトルなし)',none:'なし',
  daily_store:'デイリーストア',resets_in:'更新まで',night_market:'ナイトマーケット',ends_in:'終了まで',featured_bundle:'バンドル',accessory_store:'アクセサリーストア',kingdom_credits:'Kingdom Credits',store_unavailable:'ストアを取得できませんでした。',
  new_preset_name:'新しいプリセット名…',save_loadout:'＋ 現在のロードアウトを保存',auto_override:'自動オーバーライド',off:'オフ',by_map:'マップ別',by_agent:'エージェント別',apply_now:'適用',
  override_note:'自動オーバーライドON。VALORANT起動中、エージェントセレクト/試合に入ると、そのマップ/エージェントに割り当てたプリセットを自動適用します。',
  no_presets:'まだプリセットがありません。ロードアウトを整えて名前を入力し「現在のロードアウトを保存」を押してください。',map_label:'マップ',agent_label:'エージェント',
  gun_buddy:'ガンバディ',search_buddies:'バディを検索…',close:'閉じる',no_owned_buddies:'所持バディがありません。',
  equipped:'装備:',spray_set:'スプレー設定:',flex_set:'Flex設定:',buddy_set:'バディ設定:',buddy_removed:'バディを外しました',card_set:'カード設定:',title_set:'タイトル設定:',saved_preset:'プリセット保存:',applied_preset:'プリセット適用',refresh_hint:'（更新で反映）',rejected:'Riotに拒否されました (HTTP ',req_failed:'リクエスト失敗:',no_random:'ランダム対象のスキンがありません',save_failed:'保存に失敗:'}
};
function T(k){return (I18N[LANG]&&I18N[LANG][k])||I18N.en[k]||k;}
function setLang(l){LANG=l;try{localStorage.setItem('le_lang',l);}catch(e){} location.reload();}
let storeDailyEnd=0, storeNightEnd=0, storeAccEnd=0, lastAutoKey=null;

function toast(msg, kind){const t=document.getElementById('toast');t.textContent=msg;
  t.className='show '+(kind||'');clearTimeout(t._t);t._t=setTimeout(()=>{t.className='';},2200);}

function applyLang(){
  document.getElementById('hint').innerHTML=T('hint')+' · <b style="color:#67ed4c">build - alpha</b>';
  document.getElementById('refresh').textContent=T('refresh');
  const lb=document.getElementById('lang'); lb.textContent=LANG==='en'?'日本語':'English';
  lb.onclick=()=>setLang(LANG==='en'?'ja':'en');
  const bq=document.getElementById('bq'); if(bq) bq.placeholder=T('search_buddies');
  const bc=document.getElementById('buddy-close'); if(bc) bc.textContent=T('close');
}
async function load(){
  const r=await fetch('/api/refresh'); DATA=await r.json();
  applyLang();
  renderTabs();
  buildFilters();
  renderSidebar();
  let savedW=null; try{savedW=localStorage.getItem('le_weapon');}catch(e){}
  if(DATA.weapons.length){
    selectWeapon(weapon(savedW)?savedW:DATA.weapons[0].uuid);
    const ab=document.querySelector('.wbtn.active'); if(ab) ab.scrollIntoView({block:'center'});
  }
  renderSprays(); renderFlex();
  renderCards(); renderTitles(); renderWallet(); renderUpdate(); renderStore();
  await loadPresets(); renderPresets();
  setInterval(tickStore,1000);
  setInterval(checkAutoApply,6000);
  let savedT=null; try{savedT=localStorage.getItem('le_tab');}catch(e){}
  setView(TABS.some(t=>t[0]===savedT)?savedT:'weapons');
  document.getElementById('refresh').onclick=()=>location.reload();
  setupKeyboard();
}
function rerender(){
  renderSidebar();
  if(DATA.weapons.length){ if(!weapon(curWeapon)) curWeapon=DATA.weapons[0].uuid; selectWeapon(curWeapon); }
  if(exprSlot >= (DATA.expressions.slots.length||0)) exprSlot=0;
  renderSprays(); renderFlex(); renderCards(); renderTitles();
}
async function refresh(){
  const btn=document.getElementById('refresh'); btn.disabled=true; const old=btn.textContent; btn.textContent='⟳ …';
  try{
    const r=await fetch('/api/refresh'); const d=await r.json();
    if(d && d.error){ toast('Refresh failed: '+d.error,'err'); }
    else { DATA=d; rerender(); toast('Refreshed from Riot','ok'); }
  }catch(e){ toast('Refresh failed: '+e,'err'); }
  btn.disabled=false; btn.textContent=old;
}
function setCount(id, shown, total){const e=document.getElementById(id); if(e) e.textContent=shown+' / '+total;}
function renderWallet(){
  const w=DATA.wallet||{}, el=document.getElementById('wallet'); if(!el) return;
  const fmt=n=>(n||0).toLocaleString();
  el.innerHTML=`<span class="cur vp"><b>VP</b>${fmt(w.vp)}</span>`
    +`<span class="cur rp"><b>RP</b>${fmt(w.rp)}</span>`
    +`<span class="cur kc"><b>KC</b>${fmt(w.kc)}</span>`;
}
function renderUpdate(){
  const u=DATA.update||{}, el=document.getElementById('update'); if(!el) return;
  if(u.available){ el.textContent='⬆ '+T('update')+' '+(u.latest||''); el.href=u.url||'#'; el.style.display=''; }
  else el.style.display='none';
}
function buildFilters(){
  const wf=document.getElementById('weapon-filter');
  const tierOpts=[`<option value="">${T('all_tiers')}</option>`].concat(
    (DATA.tiers||[]).map(t=>`<option value="${t.uuid.toLowerCase()}">${t.name}</option>`)).join('');
  wf.innerHTML=`<input type="text" id="wq" placeholder="${T('search_skins')}">
    <select id="wtier">${tierOpts}</select>
    <select id="wsort"><option value="name">${T('sort_name')}</option><option value="rarity">${T('sort_rarity')}</option></select>
    <button class="chip" id="wfav">${T('favorites')}</button>
    <button class="chip" id="wbuddy">${T('buddy_btn')}</button>
    <button class="chip" id="wrandom">${T('random_btn')}</button>
    <span class="count" id="wcount"></span>`;
  document.getElementById('wq').oninput=e=>{F.weapon.q=e.target.value.toLowerCase();renderGrid();};
  document.getElementById('wtier').onchange=e=>{F.weapon.tier=e.target.value;renderGrid();};
  document.getElementById('wsort').onchange=e=>{F.weapon.sort=e.target.value;renderGrid();};
  document.getElementById('wfav').onclick=e=>{F.weapon.fav=!F.weapon.fav;e.target.classList.toggle('on',F.weapon.fav);renderGrid();};
  document.getElementById('wbuddy').onclick=openBuddyModal;
  document.getElementById('wrandom').onclick=randomSkin;
  document.getElementById('bq').oninput=e=>{buddyQ=e.target.value.toLowerCase();renderBuddyGrid();};

  const sf=document.getElementById('spray-filter');
  sf.innerHTML=`<input type="text" id="sq" placeholder="${T('search_sprays')}">
    <button class="chip" id="sanim">${T('animated')}</button>
    <span class="count" id="scount"></span>`;
  document.getElementById('sq').oninput=e=>{F.spray.q=e.target.value.toLowerCase();renderSprays();};
  document.getElementById('sanim').onclick=e=>{F.spray.animated=!F.spray.animated;e.target.classList.toggle('on',F.spray.animated);renderSprays();};

  const cf=document.getElementById('card-filter');
  cf.innerHTML=`<input type="text" id="cq" placeholder="${T('search_cards')}"><span class="count" id="ccount"></span>`;
  document.getElementById('cq').oninput=e=>{F.card.q=e.target.value.toLowerCase();renderCards();};

  const tf=document.getElementById('title-filter');
  tf.innerHTML=`<input type="text" id="tq" placeholder="${T('search_titles')}"><span class="count" id="tcount"></span>`;
  document.getElementById('tq').oninput=e=>{F.title.q=e.target.value.toLowerCase();renderTitles();};
}

function renderTabs(){
  const t=document.getElementById('tabs'); t.innerHTML='';
  TABS.forEach(([id,label])=>{
    const b=document.createElement('button'); b.className='tab'+(id===curView?' active':'');
    b.textContent=T('tab_'+id); b.onclick=()=>setView(id); t.appendChild(b);
  });
}
function setView(id){
  curView=id;
  try{localStorage.setItem('le_tab',id);}catch(e){}
  document.querySelectorAll('.tab').forEach((b,i)=>b.classList.toggle('active',TABS[i][0]===id));
  document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
  document.getElementById('view-'+id).classList.add('active');
}
function focusSearch(){const m={weapons:'wq',sprays:'sq',card:'cq',title:'tq'}[curView];
  if(m){const el=document.getElementById(m); if(el){el.focus();el.select&&el.select();}}}
function setupKeyboard(){
  document.addEventListener('keydown',e=>{
    if(e.target.tagName==='INPUT'){ if(e.key==='Escape'){e.target.value='';e.target.dispatchEvent(new Event('input'));e.target.blur();} return; }
    if(e.key>='1'&&e.key<=String(TABS.length)){const t=TABS[+e.key-1]; if(t) setView(t[0]);}
    else if(e.key==='/'){e.preventDefault(); focusSearch();}
    else if(e.key.toLowerCase()==='r'&&curView==='weapons'){randomSkin();}
  });
}

function renderSidebar(){
  const sb=document.getElementById('sidebar'); sb.innerHTML=''; let lastCat=null;
  DATA.weapons.forEach(w=>{
    if(w.categoryLabel!==lastCat){lastCat=w.categoryLabel;
      const c=document.createElement('div');c.className='cat';c.textContent=lastCat;sb.appendChild(c);}
    const b=document.createElement('button'); b.className='wbtn'+(w.uuid===curWeapon?' active':''); b.dataset.w=w.uuid;
    b.innerHTML=`<img loading="lazy" src="${w.currentSkinIcon||''}" onerror="this.style.visibility='hidden'">
      <div class="wt"><div class="wn">${w.name}</div><div class="ws" id="ws-${w.uuid}">${w.currentSkinName||''}</div></div>
      <span class="cnt">${w.skins.length}</span>`;
    b.onclick=()=>selectWeapon(w.uuid); sb.appendChild(b);
  });
}
function weapon(u){return DATA.weapons.find(w=>w.uuid===u);}
function skinOf(w,id){return id?w.skins.find(s=>s.uuid.toLowerCase()===(id||'').toLowerCase()):null;}
function selectWeapon(u){curWeapon=u;
  try{localStorage.setItem('le_weapon',u);}catch(e){}
  document.querySelectorAll('.wbtn').forEach(b=>b.classList.toggle('active',b.dataset.w===u));
  renderGrid(); renderPreview();}
function renderGrid(){
  const w=weapon(curWeapon),g=document.getElementById('grid'); g.innerHTML='';
  const list=w.skins.filter(s=>{
    if(F.weapon.q && !s.name.toLowerCase().includes(F.weapon.q)) return false;
    if(F.weapon.tier && (s.tier||'')!==F.weapon.tier) return false;
    if(F.weapon.fav && !s.favorite) return false;
    return true;
  });
  if(F.weapon.sort==='rarity') list.sort((a,b)=>(b.tierRank-a.tierRank)||a.name.localeCompare(b.name));
  else list.sort((a,b)=>a.name.localeCompare(b.name));
  setCount('wcount', list.length, w.skins.length);
  if(!w.skins.length){g.innerHTML='<div class="empty">'+T('no_skins_weapon')+'</div>';return;}
  if(!list.length){g.innerHTML='<div class="empty">'+T('no_match_filter')+'</div>';return;}
  list.forEach(s=>{
    const sel=s.uuid.toLowerCase()===(w.current.skinId||'').toLowerCase();
    const c=document.createElement('div'); c.className='card'+(sel?' sel':'');
    c.style.setProperty('--tc', s.tierColor||'#2c313d');
    const badge=s.tierIcon?`<div class="badge"><img loading="lazy" src="${s.tierIcon}" title="${s.tierName||''}"></div>`:'';
    const fav=s.favorite?'<div class="fav">★</div>':'';
    c.innerHTML=`${fav}${badge}<div class="imgwrap"><img loading="lazy" src="${s.icon||''}" onerror="this.style.visibility='hidden'"></div><div class="nm">${s.name}</div>`;
    c.onclick=()=>chooseSkin(s); g.appendChild(c);
  });
}
function randomSkin(){
  const w=weapon(curWeapon); if(!w||!w.skins.length){toast(T('no_random'),'err');return;}
  chooseSkin(w.skins[Math.floor(Math.random()*w.skins.length)]);
}
function openBuddyModal(){ if(!weapon(curWeapon))return; buddyQ=''; const bq=document.getElementById('bq'); if(bq)bq.value='';
  renderBuddyGrid(); document.getElementById('buddy-modal').classList.add('show'); if(bq)bq.focus(); }
function closeBuddyModal(){ document.getElementById('buddy-modal').classList.remove('show'); }
function renderBuddyGrid(){
  const w=weapon(curWeapon), g=document.getElementById('buddy-grid'); g.innerHTML='';
  document.getElementById('buddy-title').textContent=T('gun_buddy')+' — '+(w?w.name:'');
  const cur=(w&&w.current.charmId||'').toLowerCase();
  const none=document.createElement('div'); none.className='card spr'+(!cur?' sel':'');
  none.innerHTML='<div class="imgwrap"><span style="color:#5a606e;font-size:26px">∅</span></div><div class="nm">'+T('no_buddy')+'</div>';
  none.onclick=()=>{equipBuddy(null);closeBuddyModal();}; g.appendChild(none);
  const items=(DATA.buddies&&DATA.buddies.items)||[];
  const list=items.filter(b=>!buddyQ||b.name.toLowerCase().includes(buddyQ));
  if(!items.length){const e=document.createElement('div');e.className='empty';e.textContent=T('no_owned_buddies');g.appendChild(e);return;}
  list.forEach(b=>{
    const sel=b.uuid.toLowerCase()===cur; const c=document.createElement('div'); c.className='card spr'+(sel?' sel':'');
    c.innerHTML=`<div class="imgwrap"><img loading="lazy" src="${b.icon||''}" onerror="this.style.visibility='hidden'"></div><div class="nm">${b.name}</div>`;
    c.onclick=()=>{equipBuddy(b.uuid);closeBuddyModal();}; g.appendChild(c);
  });
}
async function equipBuddy(buddyId){
  const w=weapon(curWeapon); if(!w)return;
  const res=await post({type:'buddy',weapon:w.uuid,buddyId:buddyId});
  if(res.ok){ w.current.charmId=buddyId||null; renderPreview();
    const b=buddyId&&(DATA.buddies.items||[]).find(x=>x.uuid===buddyId);
    toast(buddyId?(T('buddy_set')+' '+(b?b.name:'')):T('buddy_removed'),'ok'); }
  else toast(T('rejected')+res.status+')','err');
}
function renderPreview(){
  const w=weapon(curWeapon),p=document.getElementById('preview'),s=skinOf(w,w.current.skinId);
  const chroma=s?(s.chromas.find(c=>c.uuid.toLowerCase()===(w.current.chromaId||'').toLowerCase())||s.chromas[0]):null;
  const img=chroma?chroma.render:(s?s.icon:w.currentSkinIcon); let v='';
  if(s){
    if(s.chromas.length>1){v+='<div class="vrow"><span class="vlbl">'+T('variant')+'</span>'+s.chromas.map(c=>{
      const sel=c.uuid.toLowerCase()===(w.current.chromaId||'').toLowerCase();
      const bg=c.swatch?`background-image:url('${c.swatch}')`:'background:#2a2e39';
      return `<button class="chroma${sel?' sel':''}" style="${bg}" ${c.owned?'':'disabled'} title="${c.name}" onclick="setChroma('${c.uuid}')"></button>`;}).join('')+'</div>';}
    if(s.levels.length>1){v+='<div class="vrow"><span class="vlbl">'+T('level')+'</span>'+s.levels.map((l,i)=>{
      const sel=l.uuid.toLowerCase()===(w.current.levelId||'').toLowerCase();
      return `<button class="lvl${sel?' sel':''}" ${l.owned?'':'disabled'} onclick="setLevel('${l.uuid}')">Lv.${i+1}</button>`;}).join('')+'</div>';}
  }
  p.style.setProperty('--tc', (s&&s.tierColor)||'#2c313d');
  const pill=(s&&s.tierName)?`<div class="pt">${s.tierIcon?`<img loading="lazy" src="${s.tierIcon}">`:''}${s.tierName}${s.favorite?' · ★':''}</div>`:'';
  const cb=w.current.charmId?(DATA.buddies.items||[]).find(b=>b.uuid.toLowerCase()===w.current.charmId.toLowerCase()):null;
  const buddy=`<div class="pt buddy" onclick="openBuddyModal()">${cb?`<img loading="lazy" src="${cb.icon}">${cb.name}`:'🧸 '+T('no_buddy')}<span class="edit">${T('edit')}</span></div>`;
  p.innerHTML=`<img loading="lazy" src="${img||''}" onerror="this.style.visibility='hidden'">
    <div class="meta"><div class="pw">${w.name}</div><div class="ps">${s?s.name:(w.currentSkinName||'Default')}</div>${pill}${buddy}</div>
    <div class="variants">${v}</div>`;
}
function highestOwnedLevel(s){let p=s.levels[0];s.levels.forEach(l=>{if(l.owned)p=l;});return p;}
async function chooseSkin(s){const w=weapon(curWeapon);
  await applySkin(w,s.uuid,highestOwnedLevel(s).uuid,s.chromas[0].uuid);}
async function setChroma(id){const w=weapon(curWeapon),s=skinOf(w,w.current.skinId);await applySkin(w,s.uuid,w.current.levelId,id);}
async function setLevel(id){const w=weapon(curWeapon),s=skinOf(w,w.current.skinId);await applySkin(w,s.uuid,id,w.current.chromaId);}
async function applySkin(w,skinId,levelId,chromaId){
  const res=await post({type:'skin',weapon:w.uuid,skinId,levelId,chromaId});
  if(res.ok){w.current={skinId,levelId,chromaId};const sk=skinOf(w,skinId);
    w.currentSkinIcon=sk?sk.icon:w.currentSkinIcon;w.currentSkinName=sk?sk.name:w.currentSkinName;
    const ws=document.getElementById('ws-'+w.uuid);if(ws)ws.textContent=w.currentSkinName;
    const im=document.querySelector(`.wbtn[data-w="${w.uuid}"] img`);if(im&&sk){im.src=sk.icon||'';im.style.visibility='visible';}
    renderGrid();renderPreview();toast(T('equipped')+' '+(sk?sk.name:'skin'),'ok');}
  else toast(T('rejected')+res.status+')','err');
}

function placeWheel(el, slots, selIdx, onSel){
  el.innerHTML='<div class="hub">'+T('expr_wheel')+'</div>'; const n=slots.length||1, R=95;
  slots.forEach((s,i)=>{
    const ang=(-90+i*360/n)*Math.PI/180, x=130+R*Math.cos(ang), y=130+R*Math.sin(ang);
    const b=document.createElement('button'); b.className='slot'+(i===selIdx?' sel':'');
    b.style.left=x+'px'; b.style.top=y+'px';
    b.innerHTML=s.icon?`<img loading="lazy" src="${s.icon}" onerror="this.style.visibility='hidden'">`:'<span style="color:#5a606e">+</span>';
    b.onclick=()=>onSel(i); el.appendChild(b);
  });
}
function exprSlots(){return DATA.expressions.slots;}
function selectExprSlot(i){exprSlot=i;renderSprays();renderFlex();}
function exprCap(kind){
  const s=exprSlots()[exprSlot];
  return s?`<b>${T('slot')} ${exprSlot+1}</b>${s.name||'Empty'}<br><span style="font-size:11px">${T(kind==='spray'?'pick_spray':'pick_flex')}</span>`:'';
}

function renderSprays(){
  placeWheel(document.getElementById('spray-wheel'),exprSlots(),exprSlot,selectExprSlot);
  document.getElementById('spray-cap').innerHTML=exprCap('spray');
  const g=document.getElementById('spray-grid'); g.innerHTML='';
  const cur=exprSlots()[exprSlot], curId=(cur&&cur.kind==='spray'&&cur.assetId||'').toLowerCase();
  const list=DATA.sprays.items.filter(s=>{
    if(F.spray.q && !s.name.toLowerCase().includes(F.spray.q)) return false;
    if(F.spray.animated && !s.animated) return false;
    return true;
  });
  setCount('scount', list.length, DATA.sprays.items.length);
  if(!list.length){g.innerHTML='<div class="empty">'+T('no_sprays')+'</div>';return;}
  list.forEach(s=>{
    const sel=s.uuid.toLowerCase()===curId; const c=document.createElement('div'); c.className='card spr'+(sel?' sel':'');
    const anim=s.animated?'<div class="fav" style="color:var(--muted)">▶</div>':'';
    c.innerHTML=`${anim}<div class="imgwrap"><img loading="lazy" src="${s.icon||''}" onerror="this.style.visibility='hidden'"></div><div class="nm">${s.name}</div>`;
    c.onclick=()=>equipExpr('spray',s); g.appendChild(c);
  });
}
function renderFlex(){
  placeWheel(document.getElementById('flex-wheel'),exprSlots(),exprSlot,selectExprSlot);
  document.getElementById('flex-cap').innerHTML=exprCap('flex');
  document.getElementById('flex-note').innerHTML='';
  const g=document.getElementById('flex-grid'); g.innerHTML='';
  const cur=exprSlots()[exprSlot], curId=(cur&&cur.kind==='flex'&&cur.assetId||'').toLowerCase();
  if(!DATA.flex.items.length){g.innerHTML='<div class="empty">'+T('no_flex')+'</div>';return;}
  DATA.flex.items.forEach(f=>{
    const sel=f.uuid.toLowerCase()===curId; const c=document.createElement('div'); c.className='card'+(sel?' sel':'');
    c.innerHTML=`<div class="imgwrap"><img loading="lazy" src="${f.icon||''}" onerror="this.style.visibility='hidden'"></div><div class="nm">${f.name}</div>`;
    c.onclick=()=>equipExpr('flex',f); g.appendChild(c);
  });
}
async function equipExpr(kind, item){
  const slot=exprSlots()[exprSlot]; if(!slot)return;
  const body={type:kind, slotIndex:exprSlot}; body[kind==='spray'?'sprayId':'flexId']=item.uuid;
  const res=await post(body);
  if(res.ok){slot.kind=kind;slot.assetId=item.uuid;slot.icon=item.icon;slot.name=item.name;
    renderSprays();renderFlex();toast(T(kind==='spray'?'spray_set':'flex_set')+' '+item.name,'ok');}
  else toast(T('rejected')+res.status+')','err');
}

function renderCards(){
  const g=document.getElementById('card-grid'); g.innerHTML='';
  const list=DATA.cards.items.filter(c=>!F.card.q || c.name.toLowerCase().includes(F.card.q));
  setCount('ccount', list.length, DATA.cards.items.length);
  if(!DATA.cards.items.length){g.innerHTML='<div class="empty">'+T('no_cards')+'</div>';return;}
  if(!list.length){g.innerHTML='<div class="empty">'+T('no_cards_match')+'</div>';return;}
  list.forEach(c=>{
    const sel=c.uuid.toLowerCase()===(DATA.cards.current||'').toLowerCase();
    const el=document.createElement('div'); el.className='card pc'+(sel?' sel':'');
    el.innerHTML=`<div class="imgwrap"><img loading="lazy" src="${c.large||c.icon||''}" onerror="this.style.visibility='hidden'"></div><div class="nm">${c.name}</div>`;
    el.onclick=()=>equipCard(c); g.appendChild(el);
  });
}
async function equipCard(c){
  const res=await post({type:'card',cardId:c.uuid});
  if(res.ok){DATA.cards.current=c.uuid;renderCards();toast(T('card_set')+' '+c.name,'ok');}
  else toast(T('rejected')+res.status+')','err');
}
function renderTitles(){
  const l=document.getElementById('title-list'); l.innerHTML='';
  const q=F.title.q;
  const list=DATA.titles.items.filter(t=>!q || (t.text||'').toLowerCase().includes(q) || (t.name||'').toLowerCase().includes(q));
  setCount('tcount', list.length, DATA.titles.items.length);
  if(!list.length){l.innerHTML='<div class="empty">'+T('no_titles_match')+'</div>';return;}
  list.forEach(t=>{
    const sel=(t.uuid||'').toLowerCase()===(DATA.titles.current||'').toLowerCase();
    const el=document.createElement('div'); el.className='trow'+(sel?' sel':'');
    el.innerHTML=`<div class="tt">${t.uuid?t.text:T('none_title')}</div><div class="tn">${t.uuid?t.name:T('none')}</div>`;
    el.onclick=()=>equipTitle(t); l.appendChild(el);
  });
}
async function equipTitle(t){
  const res=await post({type:'title',titleId:t.uuid});
  if(res.ok){DATA.titles.current=t.uuid;renderTitles();toast(T('title_set')+' '+t.text,'ok');}
  else toast(T('rejected')+res.status+')','err');
}

function fmtDur(sec){sec=Math.max(0,Math.floor(sec));const h=Math.floor(sec/3600),m=Math.floor(sec%3600/60),s=sec%60;return (h?h+'h ':'')+m+'m '+s+'s';}
function renderStore(){
  const wrap=document.getElementById('store-wrap'), st=DATA.store||{};
  if(!st.available){wrap.innerHTML='<div class="empty" style="margin-top:60px">'+T('store_unavailable')+'</div>';return;}
  storeDailyEnd=Date.now()+(st.remainingSeconds||0)*1000;
  storeNightEnd=Date.now()+(st.nightSeconds||0)*1000;
  let html='<div class="store-head"><div><div class="sh-title">'+T('daily_store')+'</div><div class="sh-sub">'+T('resets_in')+' <span id="daily-timer"></span></div></div></div>';
  html+='<div class="grid store-grid">'+(st.daily||[]).map(o=>storeCard(o,false)).join('')+'</div>';
  if(st.night&&st.night.length){
    html+='<div class="store-head night"><div><div class="sh-title">'+T('night_market')+'</div><div class="sh-sub">'+T('ends_in')+' <span id="night-timer"></span></div></div></div>';
    html+='<div class="grid store-grid">'+st.night.map(o=>storeCard(o,true)).join('')+'</div>';
  }
  (st.bundles||[]).forEach(b=>{
    const end=Date.now()+(b.seconds||0)*1000;
    html+=`<div class="store-head bundle"><div><div class="sh-title">${T('featured_bundle')} · ${b.name}</div>
      <div class="sh-sub">VP ${(b.cost==null?'—':b.cost.toLocaleString())} · ${T('ends_in')} <span class="bundle-timer" data-end="${end}"></span></div></div></div>`;
    if(b.icon) html+=`<div class="bundle-banner"><img loading="lazy" src="${b.icon}" onerror="this.style.display='none'"></div>`;
    html+='<div class="grid store-grid mini">'+(b.items||[]).map(it=>
      `<div class="card store"><div class="imgwrap"><img loading="lazy" src="${it.icon||''}" onerror="this.style.visibility='hidden'"></div><div class="nm">${it.name}</div></div>`).join('')+'</div>';
  });
  if(st.accessory&&st.accessory.length){
    storeAccEnd=Date.now()+(st.accessorySeconds||0)*1000;
    html+='<div class="store-head"><div><div class="sh-title">'+T('accessory_store')+'</div><div class="sh-sub">'+T('kingdom_credits')+' · '+T('ends_in')+' <span id="acc-timer"></span></div></div></div>';
    html+='<div class="grid store-grid">'+st.accessory.map(o=>
      `<div class="card spr"><div class="imgwrap">${o.icon?`<img loading="lazy" src="${o.icon}" onerror="this.style.visibility='hidden'">`:`<span style="color:var(--muted);font-size:12px;text-align:center;padding:8px">${o.name}</span>`}</div><div class="nm">${o.name}</div><div class="scost"><span class="vp" style="color:#7fd0c4">KC</span> <b>${o.cost==null?'—':o.cost.toLocaleString()}</b></div></div>`).join('')+'</div>';
  }
  wrap.innerHTML=html; tickStore();
}
function storeCard(o,night){
  const tc=o.tierColor||'#2c313d';
  const price=night
    ? `<span class="old">${(o.cost||0).toLocaleString()}</span> <b>${(o.discountCost||0).toLocaleString()}</b> <span class="off">-${o.percent||0}%</span>`
    : `<b>${o.cost==null?'—':o.cost.toLocaleString()}</b>`;
  const badge=o.tierIcon?`<div class="badge"><img loading="lazy" src="${o.tierIcon}"></div>`:'';
  return `<div class="card store" style="--tc:${tc}">${badge}<div class="imgwrap"><img loading="lazy" src="${o.icon||''}" onerror="this.style.visibility='hidden'"></div><div class="nm">${o.name}</div><div class="scost"><span class="vp">VP</span> ${price}</div></div>`;
}
function tickStore(){
  const dt=document.getElementById('daily-timer'); if(dt) dt.textContent=fmtDur((storeDailyEnd-Date.now())/1000);
  const nt=document.getElementById('night-timer'); if(nt) nt.textContent=fmtDur((storeNightEnd-Date.now())/1000);
  const at=document.getElementById('acc-timer'); if(at) at.textContent=fmtDur((storeAccEnd-Date.now())/1000);
  document.querySelectorAll('.bundle-timer').forEach(el=>{el.textContent=fmtDur((+el.dataset.end-Date.now())/1000);});
}

let PRESETS={presets:[],override:'off'};
async function loadPresets(){
  try{ PRESETS=await (await fetch('/api/presets')).json(); }catch(e){ PRESETS={presets:[],override:'off'}; }
  if(!Array.isArray(PRESETS.presets)) PRESETS.presets=[];
  if(!PRESETS.override) PRESETS.override='off';
  try{
    const old=JSON.parse(localStorage.getItem('le_presets')||'[]');
    if(!PRESETS.presets.length && old.length){
      PRESETS.presets=old; PRESETS.override=localStorage.getItem('le_override')||'off';
      await savePresets();
    }
    localStorage.removeItem('le_presets'); localStorage.removeItem('le_override');
  }catch(e){}
}
async function savePresets(){
  try{ await fetch('/api/presets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(PRESETS)}); }catch(e){}
}
function getPresets(){ return PRESETS.presets; }
function getOverride(){ return PRESETS.override; }
function setOverride(m){ PRESETS.override=m; lastAutoKey=null; savePresets(); renderPresets(); }
function renderPresets(){
  const wrap=document.getElementById('presets-wrap'); const ps=getPresets(); const ov=getOverride();
  const opt=(list,sel)=>['<option value="">—</option>'].concat((list||[]).map(x=>`<option value="${x.uuid}" ${x.uuid===sel?'selected':''}>${x.name}</option>`)).join('');
  let html=`<div class="filterbar">
    <input type="text" id="preset-name" placeholder="${T('new_preset_name')}">
    <button class="chip" id="preset-save">${T('save_loadout')}</button>
    <span style="margin-left:auto;color:var(--muted);font-size:12px">${T('auto_override')}</span>
    <button class="chip ${ov==='off'?'on':''}" onclick="setOverride('off')">${T('off')}</button>
    <button class="chip ${ov==='map'?'on':''}" onclick="setOverride('map')">${T('by_map')}</button>
    <button class="chip ${ov==='agent'?'on':''}" onclick="setOverride('agent')">${T('by_agent')}</button>
  </div>`;
  if(ov!=='off') html+=`<div class="note">${T('override_note')} <span id="gs-now" style="color:var(--muted)"></span></div>`;
  if(!ps.length){ html+='<div class="empty" style="margin-top:30px">'+T('no_presets')+'</div>'; }
  else {
    html+='<div class="presets-list">'+ps.map(p=>`
      <div class="preset-row">
        <div class="pname">${p.name}</div>
        <label class="psel">${T('map_label')} <select onchange="assignPreset('${p.id}','map',this.value)">${opt(DATA.maps,p.map||'')}</select></label>
        <label class="psel">${T('agent_label')} <select onchange="assignPreset('${p.id}','agent',this.value)">${opt(DATA.agents,p.agent||'')}</select></label>
        <button class="chip" onclick="applyPresetById('${p.id}')">${T('apply_now')}</button>
        <button class="chip" onclick="deletePreset('${p.id}')" title="Delete">✕</button>
      </div>`).join('')+'</div>';
  }
  wrap.innerHTML=html;
  const sv=document.getElementById('preset-save'); if(sv) sv.onclick=savePreset;
}
async function savePreset(){
  const nm=(document.getElementById('preset-name').value||'').trim()||('Preset '+(getPresets().length+1));
  try{
    const r=await fetch('/api/loadout-raw'); const lo=await r.json();
    getPresets().push({id:'p'+Date.now(), name:nm, loadout:lo, map:'', agent:''});
    await savePresets(); renderPresets(); toast(T('saved_preset')+' '+nm,'ok');
  }catch(e){ toast(T('save_failed')+' '+e,'err'); }
}
function deletePreset(id){ PRESETS.presets=getPresets().filter(p=>p.id!==id); savePresets(); renderPresets(); }
function assignPreset(id,field,val){ const p=getPresets().find(x=>x.id===id); if(p){p[field]=val; savePresets();} }
async function applyPresetById(id){ const p=getPresets().find(x=>x.id===id); if(p) await applyPresetLoadout(p.loadout, p.name); }
async function applyPresetLoadout(loadout, name){
  const res=await post2('/api/apply-preset',{loadout});
  if(res&&res.ok) toast(T('applied_preset')+(name?': '+name:'')+' '+T('refresh_hint'),'ok');
  else toast('Apply failed (HTTP '+((res&&res.status)||0)+')','err');
}
async function post2(url,body){ try{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});return await r.json();}catch(e){return {ok:false,status:0};} }
async function checkAutoApply(){
  const ov=getOverride(); if(ov==='off') return;
  let gs; try{ gs=await (await fetch('/api/gamestate')).json(); }catch(e){ return; }
  const nowEl=document.getElementById('gs-now');
  if(nowEl) nowEl.textContent='· state: '+(gs.state||'?');
  if(gs.state==='MENUS'||(!gs.map&&!gs.agent)){ lastAutoKey=null; return; }
  const key=ov==='map'?gs.map:gs.agent; if(!key) return;
  if(key===lastAutoKey) return;
  lastAutoKey=key;
  const p=getPresets().find(x=>(x[ov]||'').toLowerCase()===key.toLowerCase());
  if(p) applyPresetLoadout(p.loadout, p.name+' ('+ov+')');
}

async function post(body){
  try{const r=await fetch('/api/equip',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    return await r.json();}catch(e){toast('Request failed: '+e,'err');return {ok:false,status:0};}
}
load();
</script>
</body>
</html>
"""
