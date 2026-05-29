import os
import json
import subprocess
import shutil
import uuid
import zipfile
import re
import plistlib
from io import BytesIO
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_limiter.errors import RateLimitExceeded
from werkzeug.exceptions import RequestEntityTooLarge

try:
    from PIL import Image
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

app = Flask(__name__)
CORS(app)

# ── CONFIG ──
UPLOAD_FOLDER = "temp"
DEFAULTS_FOLDER = os.path.join(os.getcwd(), "defaults")
ABE_TOOL = os.path.join(os.getcwd(), "abe_multitool.exe" if os.name == "nt" else "abe_multitool")
UBER_SIGNER_JAR = os.path.join(os.getcwd(), "uber-apk-signer.jar")
APKTOOL_JAR = os.path.join(os.getcwd(), "apktool.jar")

ORIGINAL_PACKAGE = "com.rovio.gold"

DECODED_FOLDERS = {
    "balancing": "decoded_balancingdata",
    "event": "decoded_eventbalancingdata",
    "locale": "decoded_locale",
}

# Android icon sizes (mipmap density → pixels)
ICON_SIZES = {
    "mdpi": 48,
    "hdpi": 72,
    "xhdpi": 96,
    "xxhdpi": 144,
    "xxxhdpi": 192,
}

# iOS icon sizes (filename → pixel size)
IPA_ICON_SIZES = {
    "AppIcon-20@2x.png": 40,
    "AppIcon-20@3x.png": 60,
    "AppIcon-29@2x.png": 58,
    "AppIcon-29@3x.png": 87,
    "AppIcon-40@2x.png": 80,
    "AppIcon-40@3x.png": 120,
    "AppIcon-60@2x.png": 120,
    "AppIcon-60@3x.png": 180,
    "AppIcon-76.png": 76,
    "AppIcon-76@2x.png": 152,
    "AppIcon-83.5@2x.png": 167,
    "AppIcon-1024.png": 1024,
}

app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

# ── RATE LIMITER ──
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per hour", "30 per minute"],
    storage_uri="memory://",
)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DEFAULTS_FOLDER, exist_ok=True)

# ── STARTUP CHECKS ──
JAVA_AVAILABLE = shutil.which("java") is not None
APKTOOL_AVAILABLE = os.path.exists(APKTOOL_JAR)
if os.name != "nt" and os.path.exists(ABE_TOOL):
    try:
        os.chmod(ABE_TOOL, 0o755)
    except Exception as e:
        print(f"chmod warning: {e}")

if not JAVA_AVAILABLE:
    print("⚠️  WARNING: Java not found in PATH. APK signing/customization unavailable.")
else:
    print("✅ Java found.")
if APKTOOL_AVAILABLE:
    print("✅ apktool.jar found. Custom APK builds enabled.")
else:
    print("⚠️  apktool.jar NOT found. Custom APK builds disabled.")
if PILLOW_AVAILABLE:
    print("✅ Pillow found. Custom icon support enabled.")
else:
    print("⚠️  Pillow NOT installed. Custom icons disabled. Run: pip install Pillow")

try:
    for folder in os.listdir(UPLOAD_FOLDER):
        path = os.path.join(UPLOAD_FOLDER, folder)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    print("Cleaned up old temp folders.")
except Exception as e:
    print(f"Startup cleanup warning: {e}")

# ── DEFAULT FILES ──
DEFAULT_FILES = {
    "balancing_main": "live_SerializedBalancingDataContainer_3.0.1.bytes",
    "balancing_event": "live_SerializedEventBalancingDataContainer.bytes",
    "locale_english": "live_English.bytes",
    "apk": "Epic.apk",
    "ipa": "Epic.ipa",
}

# ── BALANCING CLASSES ──
BALANCING_CLASSES = [
    "BannerBalancingData", "BannerItemBalancingData", "BasicItemBalancingData",
    "BattleBalancingData", "BattleHintBalancingData", "BattleParticipantTableBalancingData",
    "BirdBalancingData", "BossBalancingData", "BuyableShopOfferBalancingData",
    "ChronicleCaveBattleBalancingData", "ChronicleCaveBattleParticipantTableBalancingData",
    "ChronicleCaveFloorBalancingData", "ChronicleCaveHotspotBalancingData",
    "ClassItemBalancingData", "ClassSkinBalancingData", "ClientConfigBalancingData",
    "CollectionGroupBalancingData", "ConditionalInventoryBalancingData",
    "ConsumableItemBalancingData", "CraftingItemBalancingData", "CraftingRecipeBalancingData",
    "CustomMessageBalancingData", "DailyLoginGiftsBalancingData", "EnchantingBalancingData",
    "EquipmentBalancingData", "EventItemBalancingData", "ExperienceLevelBalancingData",
    "ExperienceMasteryBalancingData", "ExperienceScalingBalancingData",
    "GachaShopOfferBalancingData", "HotspotBalancingData", "InventoryBalancingData",
    "LoadingHintBalancingData", "LootTableBalancingData", "MasteryItemBalancingData",
    "MiniCampaignBalancingData", "PigBalancingData", "PigTypePowerLevelBalancingData",
    "PowerLevelBalancingData", "PremiumShopOfferBalancingData", "PvPObjectivesBalancingData",
    "ResourceCostPerLevelBalancingData", "SalesManagerBalancingData", "ScoreBalancingData",
    "ShopBalancingData", "SkillBalancingData", "SocialEnvironmentBalancingData",
    "ThirdPartyIdBalancingData",
]

EVENT_CLASSES = [
    "EventManagerBalancingData", "EventBalancingData", "EventPlacementBalancingData",
    "PvPSeasonManagerBalancingData", "BonusEventBalancingData",
]

ALL_VALID_CLASSES = set(BALANCING_CLASSES + EVENT_CLASSES)
MAIN_CLASS_SET = set(BALANCING_CLASSES)
EVENT_CLASS_SET = set(EVENT_CLASSES)

KNOWN_BYTES_NAMES = {
    DEFAULT_FILES["balancing_main"]: "main_balancing",
    DEFAULT_FILES["balancing_event"]: "event_balancing",
    DEFAULT_FILES["locale_english"]: "locale",
}

# ── CLASS DESCRIPTIONS ──
CLASS_DESCRIPTIONS = {
    "BannerBalancingData": "Basic banner configurations (it's better to leave this alone).",
    "BannerItemBalancingData": "All stats of every banner item in the entire game are configured here.",
    "BasicItemBalancingData": "Refers to every currency, popup, and anything else you obtain (other than craftable resources and classes).",
    "BattleBalancingData": "Contains every non-chronicle-cave level in the game (including events).",
    "BattleHintBalancingData": "Contains every hint and what classes they recommend.",
    "BattleParticipantTableBalancingData": "Contains every wave in the game (except for chronicle cave waves). Also has all summon tables.",
    "BirdBalancingData": "Configurations for every bird and playable pig in the game.",
    "BossBalancingData": "Configurations for every world boss in the game.",
    "BuyableShopOfferBalancingData": "All shop offers, most buyable things, and the star rewards are here.",
    "ChronicleCaveBattleBalancingData": "Contains every chronicle cave level.",
    "ChronicleCaveBattleParticipantTableBalancingData": "Contains every chronicle cave wave.",
    "ChronicleCaveFloorBalancingData": "Basic configurations for hotspots, cave effects, and the 'boss' of the cave.",
    "ChronicleCaveHotspotBalancingData": "All hotspots in the caves, usually has an unresolved and resolved version.",
    "ClassItemBalancingData": "Every class and wearable hat in the game.",
    "ClassSkinBalancingData": "Every class upgrade in the game.",
    "ClientConfigBalancingData": "Play Store stuff is here. You can also disable ads here.",
    "CollectionGroupBalancingData": "Contains all event items.",
    "ConditionalInventoryBalancingData": "Gives an item after a specific requirement is met.",
    "ConsumableItemBalancingData": "Potion configurations.",
    "CraftingItemBalancingData": "All crafting and brewing items.",
    "CraftingRecipeBalancingData": "Points to the resource level (containing crafting requirements) that every craftable item uses.",
    "CustomMessageBalancingData": "Messages pig allies can send occasionally. Does not work in 3.0.1.",
    "DailyLoginGiftsBalancingData": "Contains all daily calendars.",
    "EnchantingBalancingData": "Configurations for enchanting costs.",
    "EquipmentBalancingData": "All weapons in the game.",
    "EventItemBalancingData": "All event portals, world bosses, and invasion collectibles/enemies are here.",
    "ExperienceLevelBalancingData": "Contains all experience required to level up and the reward from each level up.",
    "ExperienceMasteryBalancingData": "Contains all mastery required to level up your mastery and the bonus per mastery level.",
    "ExperienceScalingBalancingData": "A multiplier that increases/decreases XP gained based on your and enemy level difference.",
    "GachaShopOfferBalancingData": "Shop offers for the Golden Pig Machine. All set item chances are in LootTableBalancingData.",
    "HotspotBalancingData": "All hotspots in the game (except for caves). Usually has resolved/unresolved versions.",
    "InventoryBalancingData": "Contains what enemies and allies 'wear' (hats, swords, shields).",
    "LoadingHintBalancingData": "Contains all loading hints and the requirements to get them.",
    "LootTableBalancingData": "Contains all level wheels, chests, and many other things. ⚠️ Editing breaks event chests on old decoder versions.",
    "MasteryItemBalancingData": "All mastery items for classes.",
    "MiniCampaignBalancingData": "Basic configurations for all campaign events: hotspots, map look, and music.",
    "PigBalancingData": "Contains every enemy in the game.",
    "PigTypePowerLevelBalancingData": "The multiplier for each pig's power level.",
    "PowerLevelBalancingData": "The power modifier of every XP level in the game.",
    "PremiumShopOfferBalancingData": "Contains all MICROTRANSACTIONS in the game. ⚠️ Safe to avoid editing this.",
    "PvPObjectivesBalancingData": "Contains all arena objectives and the rewards they give.",
    "ResourceCostPerLevelBalancingData": "All crafting recipes and drops of every weapon.",
    "SalesManagerBalancingData": "All seasonal offers.",
    "ScoreBalancingData": "Contains all score rules that the game uses.",
    "ShopBalancingData": "Contains all categories that shop offers are in.",
    "SkillBalancingData": "Every attack, support, passive, cave effect, and set effect are here.",
    "SocialEnvironmentBalancingData": "All configurations for pig allies and real friend allies (back when the game was online).",
    "ThirdPartyIdBalancingData": "Refers to where money from MICROTRANSACTIONS goes. ⚠️ Remove all of it or leave it alone please.",
    "GameConstantsBalancingData": "Contains basic constants like max amount of enemies and costs of some actions.",
    "SetFusionBalancingData": "The cost of fusing and the chance of ancients.",
    "SplashScreenBalancingData": "All seasonal title screens are here.",
    "EventManagerBalancingData": "The manager of when events happen, using unix timestamps.",
    "EventBalancingData": "Contains all basic configurations of events in the game. Does not have everything.",
    "EventPlacementBalancingData": "Where event portals, world bosses, and invasion events spawn.",
    "PvPSeasonManagerBalancingData": "Configurations of every arena season in the game.",
    "BonusEventBalancingData": "Configurations of all bonus events in the game.",
}


# ── HELPERS ──
def create_work_dir():
    work_id = str(uuid.uuid4())
    work_dir = os.path.join(os.getcwd(), UPLOAD_FOLDER, work_id)
    os.makedirs(work_dir, exist_ok=True)
    return work_dir


def cleanup(work_dir):
    if os.path.exists(work_dir):
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass


def get_default_path(key):
    if key not in DEFAULT_FILES:
        return None
    path = os.path.join(DEFAULTS_FOLDER, DEFAULT_FILES[key])
    return path if os.path.exists(path) else None


def send_and_cleanup(filepath, download_name, work_dir, mimetype=None):
    with open(filepath, "rb") as f:
        data = f.read()
    cleanup(work_dir)
    return send_file(BytesIO(data), as_attachment=True, download_name=download_name, mimetype=mimetype)


def validate_extension(filename, allowed_exts):
    if not filename:
        return False
    ext = os.path.splitext(filename)[1].lower()
    return ext in [e.lower() for e in allowed_exts]


def safe_filename(filename):
    return os.path.basename(filename) if filename else ""


def extract_class_name(filename):
    if not filename or not filename.endswith(".json"):
        return None
    if filename.startswith("ABH.Shared.BalancingData."):
        return filename.replace("ABH.Shared.BalancingData.", "").replace(".json", "")
    if filename.startswith("ABH.Shared.Events.BalancingData."):
        return filename.replace("ABH.Shared.Events.BalancingData.", "").replace(".json", "")
    return None


def merge_data(original, mod):
    if isinstance(original, dict) and "Texts" in original:
        if isinstance(mod, dict) and "Texts" in mod:
            original["Texts"].update(mod["Texts"])
        return original

    if isinstance(original, list) and isinstance(mod, list):
        id_map = {}
        for i, entry in enumerate(original):
            nid = entry.get("nameId") or entry.get("NameId")
            if nid:
                id_map[nid] = i

        for mod_entry in mod:
            nid = mod_entry.get("nameId") or mod_entry.get("NameId")
            if nid and nid in id_map:
                original[id_map[nid]] = mod_entry
            else:
                original.append(mod_entry)
        return original

    return mod


def detect_ipa_bytes_path(ipa_path):
    candidates = []
    with zipfile.ZipFile(ipa_path, "r") as zf:
        for name in zf.namelist():
            if name.endswith(".bytes") and "/" in name:
                folder = name.rsplit("/", 1)[0] + "/"
                candidates.append(folder)

    if not candidates:
        return "Payload/Epic.app/Data/Raw/"

    from collections import Counter
    return Counter(candidates).most_common(1)[0][0]


def classify_mod_json(filename):
    lower = filename.lower()

    if "locale" in lower or lower.startswith("live_") or "language" in lower or "loca" in lower:
        return ("locale", None)

    class_name = extract_class_name(filename)
    if not class_name:
        class_name = filename.replace(".json", "")

    if class_name in MAIN_CLASS_SET:
        return ("main_balancing", class_name)
    if class_name in EVENT_CLASS_SET:
        return ("event_balancing", class_name)

    return (None, None)


# ── PACKAGE NAME VALIDATION & RENAMING ──
PACKAGE_NAME_REGEX = re.compile(r'^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$')


def is_valid_package_name(name):
    if not name or len(name) > 200:
        return False
    return bool(PACKAGE_NAME_REGEX.match(name))


def is_valid_app_name(name):
    """Basic validation: no XML-breaking chars, reasonable length."""
    if not name or len(name) > 80:
        return False
    if any(c in name for c in ["<", ">", "\x00"]):
        return False
    return True


def rename_package_in_decoded(decoded_dir, old_pkg, new_pkg):
    old_pkg_slash = old_pkg.replace(".", "/")
    new_pkg_slash = new_pkg.replace(".", "/")

    manifest_path = os.path.join(decoded_dir, "AndroidManifest.xml")
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace(old_pkg, new_pkg)
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write(content)

    yml_path = os.path.join(decoded_dir, "apktool.yml")
    if os.path.exists(yml_path):
        with open(yml_path, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace(old_pkg, new_pkg)
        with open(yml_path, "w", encoding="utf-8") as f:
            f.write(content)

    for root, dirs, files in os.walk(decoded_dir):
        for fname in files:
            if fname.endswith(".smali") or fname.endswith(".xml"):
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    new_content = content
                    new_content = new_content.replace(old_pkg_slash, new_pkg_slash)
                    new_content = new_content.replace(old_pkg, new_pkg)
                    if new_content != content:
                        with open(fpath, "w", encoding="utf-8") as f:
                            f.write(new_content)
                except Exception:
                    pass

    for entry in os.listdir(decoded_dir):
        if entry.startswith("smali") and os.path.isdir(os.path.join(decoded_dir, entry)):
            smali_root = os.path.join(decoded_dir, entry)
            old_path = os.path.join(smali_root, *old_pkg.split("."))
            new_path = os.path.join(smali_root, *new_pkg.split("."))

            if os.path.exists(old_path):
                os.makedirs(os.path.dirname(new_path), exist_ok=True)
                if os.path.exists(new_path):
                    for item in os.listdir(old_path):
                        shutil.move(os.path.join(old_path, item), os.path.join(new_path, item))
                    shutil.rmtree(old_path, ignore_errors=True)
                else:
                    shutil.move(old_path, new_path)

                parent = os.path.dirname(old_path)
                while parent != smali_root and os.path.exists(parent) and not os.listdir(parent):
                    os.rmdir(parent)
                    parent = os.path.dirname(parent)


def change_app_name_in_decoded(decoded_dir, new_name):
    """Update the app_name string in res/values/strings.xml (and other locales if present)."""
    res_dir = os.path.join(decoded_dir, "res")
    if not os.path.isdir(res_dir):
        return

    changed_any = False
    for entry in os.listdir(res_dir):
        if entry.startswith("values"):
            strings_path = os.path.join(res_dir, entry, "strings.xml")
            if not os.path.exists(strings_path):
                continue
            try:
                with open(strings_path, "r", encoding="utf-8") as f:
                    content = f.read()
                escaped = (new_name.replace("&", "&amp;")
                           .replace("<", "&lt;")
                           .replace(">", "&gt;")
                           .replace('"', "&quot;")
                           .replace("'", "&apos;"))
                pattern = re.compile(
                    r'(<string\s+name="app_name"[^>]*>)([^<]*)(</string>)',
                    re.DOTALL
                )
                new_content, n = pattern.subn(r'\1' + escaped + r'\3', content)
                if n > 0:
                    with open(strings_path, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    changed_any = True
            except Exception as e:
                print(f"[app-name] Failed to update {strings_path}: {e}")

    if not changed_any:
        values_dir = os.path.join(res_dir, "values")
        os.makedirs(values_dir, exist_ok=True)
        strings_path = os.path.join(values_dir, "strings.xml")
        escaped = (new_name.replace("&", "&amp;")
                   .replace("<", "&lt;")
                   .replace(">", "&gt;"))
        if os.path.exists(strings_path):
            with open(strings_path, "r", encoding="utf-8") as f:
                content = f.read()
            if "</resources>" in content:
                new_content = content.replace(
                    "</resources>",
                    f'    <string name="app_name">{escaped}</string>\n</resources>'
                )
                with open(strings_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
        else:
            with open(strings_path, "w", encoding="utf-8") as f:
                f.write(f'<?xml version="1.0" encoding="utf-8"?>\n<resources>\n    <string name="app_name">{escaped}</string>\n</resources>\n')


def replace_app_icon_in_decoded(decoded_dir, icon_path):
    """Replace ic_launcher.png in all mipmap-* and drawable-* folders with resized versions of the user's icon."""
    if not PILLOW_AVAILABLE:
        raise Exception("Pillow not installed on server (pip install Pillow)")

    res_dir = os.path.join(decoded_dir, "res")
    if not os.path.isdir(res_dir):
        raise Exception("No res/ folder found in decoded APK")

    img = Image.open(icon_path).convert("RGBA")

    w, h = img.size
    if w != h:
        size = min(w, h)
        left = (w - size) // 2
        top = (h - size) // 2
        img = img.crop((left, top, left + size, top + size))

    icon_filenames = ["ic_launcher.png", "app_icon.png", "icon.png"]

    replaced_count = 0
    for entry in os.listdir(res_dir):
        if not (entry.startswith("mipmap-") or entry.startswith("drawable-")):
            continue

        folder = os.path.join(res_dir, entry)
        if not os.path.isdir(folder):
            continue

        size_px = None
        for density, px in ICON_SIZES.items():
            if entry.endswith(f"-{density}") or entry.endswith(f"-{density}-v4"):
                size_px = px
                break

        if size_px is None:
            size_px = 96

        resized = img.resize((size_px, size_px), Image.LANCZOS)

        for fname in os.listdir(folder):
            if fname.lower() in [n.lower() for n in icon_filenames] or "ic_launcher" in fname.lower():
                fpath = os.path.join(folder, fname)
                try:
                    resized.save(fpath, "PNG", optimize=True)
                    replaced_count += 1
                except Exception as e:
                    print(f"[icon] Failed to save {fpath}: {e}")

    if replaced_count == 0:
        for density, px in ICON_SIZES.items():
            folder = os.path.join(res_dir, f"mipmap-{density}")
            os.makedirs(folder, exist_ok=True)
            resized = img.resize((px, px), Image.LANCZOS)
            resized.save(os.path.join(folder, "ic_launcher.png"), "PNG", optimize=True)
            replaced_count += 1

    print(f"[icon] Replaced/created {replaced_count} icon files")


def build_custom_apk(work_dir, base_apk_path, patched_bytes_files, do_sign,
                     new_package=None, new_app_name=None, icon_path=None):
    """Decode → customize → inject .bytes → rebuild → sign. Returns final APK path."""
    decoded_dir = os.path.join(work_dir, "decoded_apk")

    print(f"[custom-apk] Decoding APK...")
    result = subprocess.run(
        ["java", "-jar", APKTOOL_JAR, "d", base_apk_path, "-o", decoded_dir, "-f"],
        cwd=work_dir, capture_output=True, text=True, timeout=180
    )
    if result.returncode != 0:
        raise Exception(f"apktool decode failed: {result.stderr or result.stdout}")

    if new_package:
        print(f"[custom-apk] Renaming {ORIGINAL_PACKAGE} -> {new_package}...")
        rename_package_in_decoded(decoded_dir, ORIGINAL_PACKAGE, new_package)

    if new_app_name:
        print(f"[custom-apk] Setting app name to '{new_app_name}'...")
        change_app_name_in_decoded(decoded_dir, new_app_name)

    if icon_path:
        print(f"[custom-apk] Replacing app icon...")
        replace_app_icon_in_decoded(decoded_dir, icon_path)

    if patched_bytes_files:
        assets_local = os.path.join(decoded_dir, "assets", "local")
        os.makedirs(assets_local, exist_ok=True)
        for fname, fpath in patched_bytes_files.items():
            shutil.copy(fpath, os.path.join(assets_local, fname))

    print(f"[custom-apk] Rebuilding APK...")
    rebuilt_apk = os.path.join(work_dir, "rebuilt_custom.apk")
    result = subprocess.run(
        ["java", "-jar", APKTOOL_JAR, "b", decoded_dir, "-o", rebuilt_apk],
        cwd=work_dir, capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        raise Exception(f"apktool build failed: {result.stderr or result.stdout}")

    output_path = rebuilt_apk

    if do_sign:
        print(f"[custom-apk] Signing APK...")
        result = subprocess.run(
            ["java", "-jar", UBER_SIGNER_JAR, "--apks", rebuilt_apk],
            cwd=work_dir, capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            raise Exception(f"signing failed: {result.stderr or result.stdout}")

        for fname in os.listdir(work_dir):
            if "Signed" in fname and fname.endswith(".apk"):
                output_path = os.path.join(work_dir, fname)
                break

    return output_path


# ── IPA CUSTOMIZATION ──
def find_app_folder_in_ipa_extract(extract_dir):
    """Find the .app folder inside Payload/ of an extracted IPA."""
    payload = os.path.join(extract_dir, "Payload")
    if not os.path.isdir(payload):
        return None
    for entry in os.listdir(payload):
        if entry.endswith(".app"):
            return os.path.join(payload, entry)
    return None


def change_ipa_app_name(app_folder, new_name):
    """Update CFBundleDisplayName in Info.plist."""
    plist_path = os.path.join(app_folder, "Info.plist")
    if not os.path.exists(plist_path):
        raise Exception("Info.plist not found in .app folder")

    with open(plist_path, "rb") as f:
        plist = plistlib.load(f)

    plist["CFBundleDisplayName"] = new_name
    if "CFBundleName" in plist:
        plist["CFBundleName"] = new_name

    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)

    print(f"[ipa] Updated display name to '{new_name}'")


def replace_ipa_icon(app_folder, icon_path):
    """Replace all AppIcon-*.png files with resized versions of user's icon."""
    if not PILLOW_AVAILABLE:
        raise Exception("Pillow not installed on server")

    img = Image.open(icon_path).convert("RGBA")

    w, h = img.size
    if w != h:
        size = min(w, h)
        left = (w - size) // 2
        top = (h - size) // 2
        img = img.crop((left, top, left + size, top + size))

    replaced_count = 0

    for root, dirs, files in os.walk(app_folder):
        for fname in files:
            if not fname.lower().startswith("appicon") or not fname.lower().endswith(".png"):
                continue

            fpath = os.path.join(root, fname)

            target_px = IPA_ICON_SIZES.get(fname)
            if target_px is None:
                try:
                    with Image.open(fpath) as existing:
                        target_px = existing.size[0]
                except Exception:
                    target_px = 120

            resized = img.resize((target_px, target_px), Image.LANCZOS)
            # iOS icons are usually RGB (no alpha)
            if resized.mode == "RGBA":
                background = Image.new("RGB", resized.size, (255, 255, 255))
                background.paste(resized, mask=resized.split()[3])
                resized = background
            else:
                resized = resized.convert("RGB")

            try:
                resized.save(fpath, "PNG", optimize=True)
                replaced_count += 1
            except Exception as e:
                print(f"[ipa-icon] Failed to save {fpath}: {e}")

    print(f"[ipa-icon] Replaced {replaced_count} icon files")
    return replaced_count


def build_custom_ipa(work_dir, base_ipa_path, patched_bytes_files,
                     new_app_name=None, icon_path=None):
    """Extract IPA → customize Info.plist + icons → inject .bytes → rezip."""
    extract_dir = os.path.join(work_dir, "extracted_ipa")
    os.makedirs(extract_dir, exist_ok=True)

    print(f"[ipa] Extracting IPA...")
    with zipfile.ZipFile(base_ipa_path, "r") as zf:
        zf.extractall(extract_dir)

    app_folder = find_app_folder_in_ipa_extract(extract_dir)
    if not app_folder:
        raise Exception("Could not find .app folder inside Payload/")

    if new_app_name:
        change_ipa_app_name(app_folder, new_app_name)

    if icon_path:
        replace_ipa_icon(app_folder, icon_path)

    if patched_bytes_files:
        # Detect target dir based on existing .bytes files in the app
        bytes_target = None
        for root, dirs, files in os.walk(app_folder):
            for fname in files:
                if fname.endswith(".bytes"):
                    bytes_target = root
                    break
            if bytes_target:
                break

        if bytes_target is None:
            bytes_target = os.path.join(app_folder, "Data", "Raw")
            os.makedirs(bytes_target, exist_ok=True)

        for fname, fpath in patched_bytes_files.items():
            shutil.copy(fpath, os.path.join(bytes_target, fname))

    print(f"[ipa] Repacking IPA...")
    rebuilt_ipa = os.path.join(work_dir, "rebuilt_custom.ipa")
    with zipfile.ZipFile(rebuilt_ipa, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(extract_dir):
            for fname in files:
                full_path = os.path.join(root, fname)
                arcname = os.path.relpath(full_path, extract_dir)
                zf.write(full_path, arcname)

    return rebuilt_ipa


# ── ERROR HANDLERS ──
@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    return jsonify({"error": "File too large. Max upload size is 50 MB."}), 413


@app.errorhandler(RateLimitExceeded)
def handle_rate_limit(e):
    return jsonify({"error": f"Rate limit exceeded: {e.description}. Please slow down."}), 429


# ── ROUTES ──
@app.route("/")
@limiter.exempt
def home():
    return send_file("index.html")


@app.route("/api/classes", methods=["GET"])
@limiter.exempt
def list_classes():
    return jsonify({"balancing": BALANCING_CLASSES, "event": EVENT_CLASSES})


@app.route("/api/descriptions", methods=["GET"])
@limiter.exempt
def get_descriptions():
    return jsonify(CLASS_DESCRIPTIONS)


@app.route("/api/defaults", methods=["GET"])
@limiter.exempt
def list_defaults():
    available = {}
    for key, filename in DEFAULT_FILES.items():
        path = os.path.join(DEFAULTS_FOLDER, filename)
        exists = os.path.exists(path)
        available[key] = {
            "filename": filename,
            "available": exists,
            "size_mb": round(os.path.getsize(path) / (1024 * 1024), 2) if exists else 0,
        }
    return jsonify(available)


@app.route("/api/capabilities", methods=["GET"])
@limiter.exempt
def get_capabilities():
    return jsonify({
        "java": JAVA_AVAILABLE,
        "apktool": APKTOOL_AVAILABLE,
        "pillow": PILLOW_AVAILABLE,
        "custom_apk": JAVA_AVAILABLE and APKTOOL_AVAILABLE,
        "custom_icon": JAVA_AVAILABLE and APKTOOL_AVAILABLE and PILLOW_AVAILABLE,
        "original_package": ORIGINAL_PACKAGE,
    })


@app.route("/api/defaults/<key>", methods=["GET"])
@limiter.limit("10 per minute")
def get_default(key):
    path = get_default_path(key)
    if not path:
        return jsonify({"error": "Default file not available"}), 404
    return send_file(path, as_attachment=True, download_name=DEFAULT_FILES[key])


# ── PRE-DECODED DOWNLOADS ──
@app.route("/api/decoded/list", methods=["GET"])
@limiter.exempt
def list_decoded():
    result = {}
    for category, folder_name in DECODED_FOLDERS.items():
        folder_path = os.path.join(DEFAULTS_FOLDER, folder_name)
        files = []
        if os.path.isdir(folder_path):
            for fname in sorted(os.listdir(folder_path)):
                if fname.endswith(".json"):
                    fpath = os.path.join(folder_path, fname)
                    files.append({
                        "filename": fname,
                        "size_kb": round(os.path.getsize(fpath) / 1024, 1),
                    })
        result[category] = files
    return jsonify(result)


@app.route("/api/decoded/<category>/all", methods=["GET"])
@limiter.limit("5 per minute")
def download_decoded_all(category):
    if category not in DECODED_FOLDERS:
        return jsonify({"error": "Invalid category"}), 404

    folder = os.path.join(DEFAULTS_FOLDER, DECODED_FOLDERS[category])
    if not os.path.isdir(folder):
        return jsonify({"error": "Folder not found"}), 404

    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in sorted(os.listdir(folder)):
            if fname.endswith(".json"):
                zf.write(os.path.join(folder, fname), fname)
    zip_buffer.seek(0)

    return send_file(zip_buffer, as_attachment=True,
                     download_name=f"{category}_decoded.zip", mimetype="application/zip")


@app.route("/api/decoded/<category>/<filename>", methods=["GET"])
@limiter.limit("60 per minute")
def download_decoded(category, filename):
    if category not in DECODED_FOLDERS:
        return jsonify({"error": "Invalid category"}), 404

    safe_name = safe_filename(filename)
    if not safe_name.endswith(".json"):
        return jsonify({"error": "Only .json files allowed"}), 400

    folder = os.path.join(DEFAULTS_FOLDER, DECODED_FOLDERS[category])
    file_path = os.path.join(folder, safe_name)

    if not os.path.exists(file_path):
        return jsonify({"error": "File not found"}), 404

    return send_file(file_path, as_attachment=True, download_name=safe_name)


# ── INLINE EDITOR PATCH ROUTES ──
@app.route("/api/patch/balancing/inline", methods=["POST"])
@limiter.limit("20 per minute")
def patch_balancing_inline():
    work_dir = create_work_dir()
    try:
        data = request.get_json(silent=True)
        if not data:
            cleanup(work_dir)
            return jsonify({"error": "No JSON body provided"}), 400

        class_name = data.get("class_name")
        target = data.get("target", "main")
        mod_json = data.get("content")
        mode = data.get("mode", "replace")

        if not class_name:
            cleanup(work_dir)
            return jsonify({"error": "No class_name provided"}), 400

        if class_name not in ALL_VALID_CLASSES:
            cleanup(work_dir)
            return jsonify({"error": f"Invalid class name: {class_name}"}), 400

        if mod_json is None:
            cleanup(work_dir)
            return jsonify({"error": "No content provided"}), 400

        if mode not in ("replace", "merge"):
            cleanup(work_dir)
            return jsonify({"error": "Mode must be 'replace' or 'merge'"}), 400

        if target == "main":
            default_key = "balancing_main"
            decoded_folder = os.path.join(DEFAULTS_FOLDER, DECODED_FOLDERS["balancing"])
            file_prefix = "ABH.Shared.BalancingData."
        elif target == "event":
            default_key = "balancing_event"
            decoded_folder = os.path.join(DEFAULTS_FOLDER, DECODED_FOLDERS["event"])
            file_prefix = "ABH.Shared.Events.BalancingData."
        else:
            cleanup(work_dir)
            return jsonify({"error": "Invalid target"}), 400

        default_path = get_default_path(default_key)
        if not default_path:
            cleanup(work_dir)
            return jsonify({"error": f"Default '{default_key}' not available"}), 404

        original_filename = DEFAULT_FILES[default_key]
        bytes_path = os.path.join(work_dir, original_filename)
        shutil.copy(default_path, bytes_path)

        decoded_path = os.path.join(work_dir, f"{file_prefix}{class_name}.json")

        if mode == "merge":
            base_decoded = os.path.join(decoded_folder, f"{file_prefix}{class_name}.json")
            if not os.path.exists(base_decoded):
                cleanup(work_dir)
                return jsonify({"error": "No pre-decoded base file found for merge mode"}), 404

            with open(base_decoded, "r", encoding="utf-8") as f:
                original_data = json.load(f)

            merged = merge_data(original_data, mod_json)

            with open(decoded_path, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2, ensure_ascii=False)
        else:
            with open(decoded_path, "w", encoding="utf-8") as f:
                json.dump(mod_json, f, indent=2, ensure_ascii=False)

        output_path = os.path.join(work_dir, "patched.bytes")
        result = subprocess.run(
            [ABE_TOOL, "balancing", bytes_path, class_name, "encode", decoded_path, output_path],
            cwd=work_dir, capture_output=True, text=True
        )
        if result.returncode != 0:
            cleanup(work_dir)
            return jsonify({"error": f"Encode failed: {result.stderr}"}), 500

        return send_and_cleanup(output_path, original_filename, work_dir)

    except Exception as e:
        cleanup(work_dir)
        return jsonify({"error": str(e)}), 500


@app.route("/api/patch/locale/inline", methods=["POST"])
@limiter.limit("20 per minute")
def patch_locale_inline():
    work_dir = create_work_dir()
    try:
        data = request.get_json(silent=True)
        if not data:
            cleanup(work_dir)
            return jsonify({"error": "No JSON body provided"}), 400

        mod_json = data.get("content")
        mode = data.get("mode", "replace")

        if mod_json is None:
            cleanup(work_dir)
            return jsonify({"error": "No content provided"}), 400

        if mode not in ("replace", "merge"):
            cleanup(work_dir)
            return jsonify({"error": "Mode must be 'replace' or 'merge'"}), 400

        default_path = get_default_path("locale_english")
        if not default_path:
            cleanup(work_dir)
            return jsonify({"error": "Default locale not available"}), 404

        original_filename = DEFAULT_FILES["locale_english"]
        bytes_path = os.path.join(work_dir, original_filename)
        shutil.copy(default_path, bytes_path)

        decoded_path = os.path.join(work_dir, "decoded_locale.json")

        if mode == "merge":
            result = subprocess.run(
                [ABE_TOOL, "locale", "decode", bytes_path, decoded_path],
                cwd=work_dir, capture_output=True, text=True
            )
            if result.returncode != 0:
                cleanup(work_dir)
                return jsonify({"error": f"Decode failed: {result.stderr}"}), 500

            with open(decoded_path, "r", encoding="utf-8") as f:
                original = json.load(f)

            merged = merge_data(original, mod_json)

            with open(decoded_path, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2, ensure_ascii=False)
        else:
            with open(decoded_path, "w", encoding="utf-8") as f:
                json.dump(mod_json, f, indent=2, ensure_ascii=False)

        output_path = os.path.join(work_dir, "patched.bytes")
        result = subprocess.run(
            [ABE_TOOL, "locale", "encode", decoded_path, output_path],
            cwd=work_dir, capture_output=True, text=True
        )
        if result.returncode != 0:
            cleanup(work_dir)
            return jsonify({"error": f"Encode failed: {result.stderr}"}), 500

        return send_and_cleanup(output_path, original_filename, work_dir)

    except Exception as e:
        cleanup(work_dir)
        return jsonify({"error": str(e)}), 500


# ── Custom build option validation (for both APK and IPA) ──
def validate_custom_options(output, custom_package, custom_app_name, has_icon):
    """Returns (error_message, has_any_customization) tuple. error_message is None if all good."""
    has_any = bool(custom_package or custom_app_name or has_icon)

    if not has_any:
        return (None, False)

    if output not in ("apk", "ipa"):
        return ("Customization options only apply to APK or IPA output.", True)

    # Package name is APK-only
    if custom_package and output != "apk":
        return ("Custom package name only applies to APK output. IPA Bundle IDs are overridden by sideloaders.", True)

    if output == "apk" and not APKTOOL_AVAILABLE:
        return ("Custom APK builds unavailable (apktool.jar missing on server).", True)

    if custom_package:
        if not is_valid_package_name(custom_package):
            return (f"Invalid package name '{custom_package}'. Must be lowercase, dot-separated (e.g. com.yourname.epicmod).", True)
        if custom_package == ORIGINAL_PACKAGE:
            return ("Custom package name must be different from the original.", True)

    if custom_app_name and not is_valid_app_name(custom_app_name):
        return ("Invalid app name (max 80 chars, no XML special chars).", True)

    if has_icon and not PILLOW_AVAILABLE:
        return ("Custom icon unavailable (Pillow not installed on server).", True)

    return (None, True)


def save_icon_file(work_dir, icon_file):
    """Save uploaded icon file and validate it. Returns path or raises."""
    fname = safe_filename(icon_file.filename)
    if not validate_extension(fname, [".png", ".jpg", ".jpeg", ".webp"]):
        raise Exception("Icon must be PNG, JPG, or WEBP")

    icon_path = os.path.join(work_dir, "custom_icon" + os.path.splitext(fname)[1].lower())
    icon_file.save(icon_path)

    if PILLOW_AVAILABLE:
        try:
            with Image.open(icon_path) as img:
                img.verify()
        except Exception as e:
            raise Exception(f"Invalid image file: {e}")

    return icon_path


# ── 🎁 BUILD: JSON mode ──
@app.route("/api/build/json", methods=["POST"])
@limiter.limit("5 per minute")
def build_from_json():
    work_dir = create_work_dir()
    try:
        mod_files = request.files.getlist("mod_files")
        output = request.form.get("output", "bytes")
        do_sign = request.form.get("sign", "true").lower() == "true"
        mode = request.form.get("mode", "merge")
        custom_package = (request.form.get("custom_package") or "").strip()
        custom_app_name = (request.form.get("custom_app_name") or "").strip()
        icon_file = request.files.get("custom_icon")

        if not mod_files:
            cleanup(work_dir)
            return jsonify({"error": "No mod files uploaded"}), 400

        if output not in ("bytes", "apk", "ipa"):
            cleanup(work_dir)
            return jsonify({"error": "Output must be 'bytes', 'apk', or 'ipa'"}), 400

        if mode not in ("merge", "replace"):
            cleanup(work_dir)
            return jsonify({"error": "Mode must be 'merge' or 'replace'"}), 400

        if output == "apk" and do_sign and not JAVA_AVAILABLE:
            cleanup(work_dir)
            return jsonify({
                "error": "APK signing unavailable on this server (Java not installed)."
            }), 503

        err, has_customization = validate_custom_options(output, custom_package, custom_app_name, bool(icon_file))
        if err:
            cleanup(work_dir)
            return jsonify({"error": err}), 400

        icon_path = None
        if icon_file:
            try:
                icon_path = save_icon_file(work_dir, icon_file)
            except Exception as e:
                cleanup(work_dir)
                return jsonify({"error": str(e)}), 400

        main_mods = {}
        event_mods = {}
        locale_mods = []
        skipped = []

        for mf in mod_files:
            fname = safe_filename(mf.filename)

            if not validate_extension(fname, [".json"]):
                skipped.append({"file": fname, "reason": "not a .json file"})
                continue

            category, class_name = classify_mod_json(fname)

            if category is None:
                skipped.append({"file": fname, "reason": "couldn't determine target"})
                continue

            if category == "main_balancing":
                main_mods[class_name] = mf
            elif category == "event_balancing":
                event_mods[class_name] = mf
            elif category == "locale":
                locale_mods.append(mf)

        if not main_mods and not event_mods and not locale_mods:
            cleanup(work_dir)
            return jsonify({"error": "No valid mod files found.", "skipped": skipped}), 400

        applied = {"main": [], "event": [], "locale": []}
        patched_bytes_files = {}

        # Patch main balancing
        if main_mods:
            default_path = get_default_path("balancing_main")
            decoded_folder = os.path.join(DEFAULTS_FOLDER, DECODED_FOLDERS["balancing"])

            if not default_path:
                cleanup(work_dir)
                return jsonify({"error": "Default main balancing not available"}), 404

            original_filename = DEFAULT_FILES["balancing_main"]
            bytes_path = os.path.join(work_dir, original_filename)
            shutil.copy(default_path, bytes_path)

            for class_name, mod_file in main_mods.items():
                mod_path = os.path.join(work_dir, f"main_mod_{class_name}.json")
                mod_file.save(mod_path)

                try:
                    with open(mod_path, "r", encoding="utf-8") as f:
                        mod_data = json.load(f)
                except json.JSONDecodeError as e:
                    skipped.append({"file": mod_file.filename, "reason": f"invalid JSON: {e.msg}"})
                    continue

                decoded_path = os.path.join(work_dir, f"ABH.Shared.BalancingData.{class_name}.json")

                if mode == "merge":
                    base_decoded = os.path.join(decoded_folder, f"ABH.Shared.BalancingData.{class_name}.json")
                    if not os.path.exists(base_decoded):
                        skipped.append({"file": mod_file.filename, "reason": "no pre-decoded base for merge"})
                        continue

                    with open(base_decoded, "r", encoding="utf-8") as f:
                        original_data = json.load(f)

                    merged = merge_data(original_data, mod_data)

                    with open(decoded_path, "w", encoding="utf-8") as f:
                        json.dump(merged, f, indent=2, ensure_ascii=False)
                else:
                    with open(decoded_path, "w", encoding="utf-8") as f:
                        json.dump(mod_data, f, indent=2, ensure_ascii=False)

                result = subprocess.run(
                    [ABE_TOOL, "balancing", bytes_path, class_name, "encode", decoded_path, bytes_path],
                    cwd=work_dir, capture_output=True, text=True
                )
                if result.returncode != 0:
                    skipped.append({"file": mod_file.filename, "reason": f"encode failed: {result.stderr.strip()}"})
                    continue

                applied["main"].append(class_name)

            if applied["main"]:
                patched_bytes_files[original_filename] = bytes_path

        # Patch event balancing
        if event_mods:
            default_path = get_default_path("balancing_event")
            decoded_folder = os.path.join(DEFAULTS_FOLDER, DECODED_FOLDERS["event"])

            if not default_path:
                cleanup(work_dir)
                return jsonify({"error": "Default event balancing not available"}), 404

            original_filename = DEFAULT_FILES["balancing_event"]
            bytes_path = os.path.join(work_dir, original_filename)
            shutil.copy(default_path, bytes_path)

            for class_name, mod_file in event_mods.items():
                mod_path = os.path.join(work_dir, f"event_mod_{class_name}.json")
                mod_file.save(mod_path)

                try:
                    with open(mod_path, "r", encoding="utf-8") as f:
                        mod_data = json.load(f)
                except json.JSONDecodeError as e:
                    skipped.append({"file": mod_file.filename, "reason": f"invalid JSON: {e.msg}"})
                    continue

                decoded_path = os.path.join(work_dir, f"ABH.Shared.Events.BalancingData.{class_name}.json")

                if mode == "merge":
                    base_decoded = os.path.join(decoded_folder, f"ABH.Shared.Events.BalancingData.{class_name}.json")
                    if not os.path.exists(base_decoded):
                        skipped.append({"file": mod_file.filename, "reason": "no pre-decoded base for merge"})
                        continue

                    with open(base_decoded, "r", encoding="utf-8") as f:
                        original_data = json.load(f)

                    merged = merge_data(original_data, mod_data)

                    with open(decoded_path, "w", encoding="utf-8") as f:
                        json.dump(merged, f, indent=2, ensure_ascii=False)
                else:
                    with open(decoded_path, "w", encoding="utf-8") as f:
                        json.dump(mod_data, f, indent=2, ensure_ascii=False)

                result = subprocess.run(
                    [ABE_TOOL, "balancing", bytes_path, class_name, "encode", decoded_path, bytes_path],
                    cwd=work_dir, capture_output=True, text=True
                )
                if result.returncode != 0:
                    skipped.append({"file": mod_file.filename, "reason": f"encode failed: {result.stderr.strip()}"})
                    continue

                applied["event"].append(class_name)

            if applied["event"]:
                patched_bytes_files[original_filename] = bytes_path

        # Patch locale
        if locale_mods:
            default_path = get_default_path("locale_english")
            if not default_path:
                cleanup(work_dir)
                return jsonify({"error": "Default locale not available"}), 404

            original_filename = DEFAULT_FILES["locale_english"]
            bytes_path = os.path.join(work_dir, original_filename)
            shutil.copy(default_path, bytes_path)

            decoded_path = os.path.join(work_dir, "decoded_locale.json")

            result = subprocess.run(
                [ABE_TOOL, "locale", "decode", bytes_path, decoded_path],
                cwd=work_dir, capture_output=True, text=True
            )
            if result.returncode != 0:
                skipped.append({"file": "locale", "reason": f"decode failed: {result.stderr.strip()}"})
            else:
                with open(decoded_path, "r", encoding="utf-8") as f:
                    locale_data = json.load(f)

                for mod_file in locale_mods:
                    mod_path = os.path.join(work_dir, f"locale_mod_{uuid.uuid4().hex[:6]}.json")
                    mod_file.save(mod_path)

                    try:
                        with open(mod_path, "r", encoding="utf-8") as f:
                            mod_data = json.load(f)
                    except json.JSONDecodeError as e:
                        skipped.append({"file": mod_file.filename, "reason": f"invalid JSON: {e.msg}"})
                        continue

                    if mode == "merge":
                        locale_data = merge_data(locale_data, mod_data)
                    else:
                        locale_data = mod_data

                    applied["locale"].append(mod_file.filename)

                with open(decoded_path, "w", encoding="utf-8") as f:
                    json.dump(locale_data, f, indent=2, ensure_ascii=False)

                result = subprocess.run(
                    [ABE_TOOL, "locale", "encode", decoded_path, bytes_path],
                    cwd=work_dir, capture_output=True, text=True
                )
                if result.returncode != 0:
                    skipped.append({"file": "locale", "reason": f"encode failed: {result.stderr.strip()}"})
                else:
                    patched_bytes_files[original_filename] = bytes_path

        if not patched_bytes_files:
            cleanup(work_dir)
            return jsonify({"error": "No mods were successfully applied.", "skipped": skipped}), 400

        return _build_output(work_dir, patched_bytes_files, output, do_sign, applied, skipped,
                             custom_package, custom_app_name, icon_path)

    except Exception as e:
        cleanup(work_dir)
        return jsonify({"error": str(e)}), 500


# ── 🎁 BUILD: Bytes mode ──
@app.route("/api/build/bytes", methods=["POST"])
@limiter.limit("5 per minute")
def build_from_bytes():
    work_dir = create_work_dir()
    try:
        bytes_files = request.files.getlist("bytes_files")
        output = request.form.get("output", "apk")
        do_sign = request.form.get("sign", "true").lower() == "true"
        custom_package = (request.form.get("custom_package") or "").strip()
        custom_app_name = (request.form.get("custom_app_name") or "").strip()
        icon_file = request.files.get("custom_icon")

        if not bytes_files:
            cleanup(work_dir)
            return jsonify({"error": "No bytes files uploaded"}), 400

        if output not in ("bytes", "apk", "ipa"):
            cleanup(work_dir)
            return jsonify({"error": "Output must be 'bytes', 'apk', or 'ipa'"}), 400

        if output == "apk" and do_sign and not JAVA_AVAILABLE:
            cleanup(work_dir)
            return jsonify({
                "error": "APK signing unavailable on this server (Java not installed)."
            }), 503

        err, has_customization = validate_custom_options(output, custom_package, custom_app_name, bool(icon_file))
        if err:
            cleanup(work_dir)
            return jsonify({"error": err}), 400

        icon_path = None
        if icon_file:
            try:
                icon_path = save_icon_file(work_dir, icon_file)
            except Exception as e:
                cleanup(work_dir)
                return jsonify({"error": str(e)}), 400

        patched_bytes_files = {}
        skipped = []
        applied = {"main": [], "event": [], "locale": [], "other": []}

        for bf in bytes_files:
            fname = safe_filename(bf.filename)

            if not validate_extension(fname, [".bytes"]):
                skipped.append({"file": fname, "reason": "not a .bytes file"})
                continue

            saved_path = os.path.join(work_dir, fname)
            bf.save(saved_path)
            patched_bytes_files[fname] = saved_path

            if fname in KNOWN_BYTES_NAMES:
                category = KNOWN_BYTES_NAMES[fname]
                if category == "main_balancing":
                    applied["main"].append(fname)
                elif category == "event_balancing":
                    applied["event"].append(fname)
                elif category == "locale":
                    applied["locale"].append(fname)
            else:
                applied["other"].append(fname)

        if not patched_bytes_files:
            cleanup(work_dir)
            return jsonify({"error": "No valid .bytes files uploaded.", "skipped": skipped}), 400

        return _build_output(work_dir, patched_bytes_files, output, do_sign, applied, skipped,
                             custom_package, custom_app_name, icon_path)

    except Exception as e:
        cleanup(work_dir)
        return jsonify({"error": str(e)}), 500


def _build_output(work_dir, patched_bytes_files, output, do_sign, applied, skipped,
                  custom_package="", custom_app_name="", icon_path=None):
    """Shared output-building logic."""
    has_customization = bool(custom_package or custom_app_name or icon_path)

    if output == "bytes":
        zip_path = os.path.join(work_dir, "modded_bytes.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname, fpath in patched_bytes_files.items():
                zf.write(fpath, fname)

        with open(zip_path, "rb") as f:
            data = f.read()
        cleanup(work_dir)

        response = send_file(
            BytesIO(data),
            as_attachment=True,
            download_name="modded_bytes.zip",
            mimetype="application/zip"
        )
        _attach_headers(response, applied, skipped)
        return response

    elif output == "apk":
        apk_default = get_default_path("apk")
        if not apk_default:
            cleanup(work_dir)
            return jsonify({"error": "Default APK not available"}), 404

        apk_path = os.path.join(work_dir, "base.apk")
        shutil.copy(apk_default, apk_path)

        # ── CUSTOM APK PATH (slow, uses apktool) ──
        if has_customization:
            try:
                output_path = build_custom_apk(
                    work_dir, apk_path, patched_bytes_files, do_sign,
                    new_package=custom_package or None,
                    new_app_name=custom_app_name or None,
                    icon_path=icon_path,
                )
            except Exception as e:
                cleanup(work_dir)
                return jsonify({"error": f"Custom APK build failed: {str(e)}"}), 500

            name_parts = ["modded"]
            if custom_package:
                name_parts.append(custom_package)
            elif custom_app_name:
                safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', custom_app_name)[:40]
                name_parts.append(safe_name)
            download_name = "_".join(name_parts) + ".apk"

            with open(output_path, "rb") as f:
                data = f.read()
            cleanup(work_dir)
            response = send_file(BytesIO(data), as_attachment=True, download_name=download_name)
            _attach_headers(response, applied, skipped)
            if custom_package:
                response.headers["X-Custom-Package"] = custom_package
            if custom_app_name:
                response.headers["X-Custom-App-Name"] = custom_app_name
            if icon_path:
                response.headers["X-Custom-Icon"] = "true"
            return response

        # ── NORMAL FAST PATH (zip injection) ──
        target_dir = "assets/local/"
        target_filenames = set(patched_bytes_files.keys())

        rebuilt_apk = os.path.join(work_dir, "rebuilt.apk")
        with zipfile.ZipFile(apk_path, "r") as old_zip:
            with zipfile.ZipFile(rebuilt_apk, "w", zipfile.ZIP_DEFLATED) as new_zip:
                for item in old_zip.namelist():
                    if any(item == target_dir + fn for fn in target_filenames):
                        continue
                    new_zip.writestr(item, old_zip.read(item))

                for fname, fpath in patched_bytes_files.items():
                    new_zip.write(fpath, target_dir + fname)

        output_path = rebuilt_apk

        if do_sign:
            result = subprocess.run(
                ["java", "-jar", UBER_SIGNER_JAR, "--apks", rebuilt_apk],
                cwd=work_dir, capture_output=True, text=True
            )
            if result.returncode != 0:
                cleanup(work_dir)
                return jsonify({"error": f"Signing failed: {result.stderr}"}), 500

            for fname in os.listdir(work_dir):
                if "Signed" in fname and fname.endswith(".apk"):
                    output_path = os.path.join(work_dir, fname)
                    break

        with open(output_path, "rb") as f:
            data = f.read()
        cleanup(work_dir)

        response = send_file(
            BytesIO(data),
            as_attachment=True,
            download_name=f"modded_{DEFAULT_FILES['apk']}"
        )
        _attach_headers(response, applied, skipped)
        return response

    elif output == "ipa":
        ipa_default = get_default_path("ipa")
        if not ipa_default:
            cleanup(work_dir)
            return jsonify({"error": "Default IPA not available"}), 404

        ipa_path = os.path.join(work_dir, "base.ipa")
        shutil.copy(ipa_default, ipa_path)

        # IPA can customize name + icon (not package)
        has_ipa_customization = bool(custom_app_name or icon_path)

        if has_ipa_customization:
            try:
                rebuilt_ipa = build_custom_ipa(
                    work_dir, ipa_path, patched_bytes_files,
                    new_app_name=custom_app_name or None,
                    icon_path=icon_path,
                )
            except Exception as e:
                cleanup(work_dir)
                return jsonify({"error": f"Custom IPA build failed: {str(e)}"}), 500

            name_parts = ["modded"]
            if custom_app_name:
                safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', custom_app_name)[:40]
                name_parts.append(safe_name)
            download_name = "_".join(name_parts) + ".ipa"

            with open(rebuilt_ipa, "rb") as f:
                data = f.read()
            cleanup(work_dir)
            response = send_file(BytesIO(data), as_attachment=True, download_name=download_name)
            _attach_headers(response, applied, skipped)
            if custom_app_name:
                response.headers["X-Custom-App-Name"] = custom_app_name
            if icon_path:
                response.headers["X-Custom-Icon"] = "true"
            return response

        # ── Fast path: just zip-inject ──
        target_dir = detect_ipa_bytes_path(ipa_path)
        target_filenames = set(patched_bytes_files.keys())

        rebuilt_ipa = os.path.join(work_dir, "rebuilt.ipa")
        with zipfile.ZipFile(ipa_path, "r") as old_zip:
            with zipfile.ZipFile(rebuilt_ipa, "w", zipfile.ZIP_DEFLATED) as new_zip:
                for item in old_zip.namelist():
                    if any(item == target_dir + fn for fn in target_filenames):
                        continue
                    new_zip.writestr(item, old_zip.read(item))

                for fname, fpath in patched_bytes_files.items():
                    new_zip.write(fpath, target_dir + fname)

        with open(rebuilt_ipa, "rb") as f:
            data = f.read()
        cleanup(work_dir)

        response = send_file(
            BytesIO(data),
            as_attachment=True,
            download_name=f"modded_{DEFAULT_FILES['ipa']}"
        )
        _attach_headers(response, applied, skipped)
        return response


def _attach_headers(response, applied, skipped):
    response.headers["X-Applied-Main"] = ",".join(applied.get("main", []))
    response.headers["X-Applied-Event"] = ",".join(applied.get("event", []))
    response.headers["X-Applied-Locale"] = str(len(applied.get("locale", [])))
    response.headers["X-Applied-Other"] = ",".join(applied.get("other", []))
    response.headers["X-Skipped-Count"] = str(len(skipped))
    response.headers["Access-Control-Expose-Headers"] = "X-Applied-Main,X-Applied-Event,X-Applied-Locale,X-Applied-Other,X-Skipped-Count,X-Custom-Package,X-Custom-App-Name,X-Custom-Icon,Content-Disposition"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    print("=" * 50)
    print(f"AB Epic Patcher running on port {port}")
    print(f"Defaults folder: {DEFAULTS_FOLDER}")
    print(f"Max upload size: {app.config['MAX_CONTENT_LENGTH'] // (1024 * 1024)} MB")
    print(f"Java: {'✅' if JAVA_AVAILABLE else '❌'}")
    print(f"Apktool: {'✅' if APKTOOL_AVAILABLE else '❌'}")
    print(f"Pillow (custom icons): {'✅' if PILLOW_AVAILABLE else '❌'}")
    print(f"Debug mode: {'ON' if debug_mode else 'OFF'}")
    print("=" * 50)
    app.run(host="0.0.0.0", port=port, debug=debug_mode)