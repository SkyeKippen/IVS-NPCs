#!/usr/bin/env python3
"""Check that every profile, spawn group, prefab and behavior name referenced in
the mod's .sbc files is actually defined somewhere.

Usage: python3 tools/check_references.py [content_dir]

Errors (exit code 1):
  - an .sbc file that is not valid XML
  - a reference to a name that is not defined
  - a reference to a name defined as the wrong kind (e.g. [Triggers:] naming an Action)
  - the same name defined twice
  - a RivalAI Spawn profile naming a SpawnGroup whose <Frequency> is 0 (MES
    skips Frequency 0 groups, so the spawn silently fails)
Warnings (exit code 0):
  - profiles and prefabs that nothing references
"""
import os
import re
import sys
import xml.etree.ElementTree as ET

# Header tag at the top of a <Description> -> kind of definition.
HEADERS = {
    "Modular Encounters SpawnGroup": "SpawnGroup",
    "MES Spawn Conditions": "SpawnConditions",
    "MES Manipulation": "Manipulation",
    "MES Loot": "Loot",
    "RivalAI Behavior": "Behavior",
    "RivalAI Autopilot": "Autopilot",
    "RivalAI Target": "Target",
    "RivalAI Weapons": "Weapons",
    "RivalAI Trigger": "Trigger",
    "RivalAI TriggerGroup": "TriggerGroup",
    "RivalAI Action": "Action",
    "RivalAI Chat": "Chat",
    "RivalAI Spawn": "Spawn",
    "RivalAI Condition": "Condition",
}

# Description tags whose (comma separated) values name another definition.
REF_KEYS = {
    "SpawnConditionsProfiles": "SpawnConditions",
    "ManipulationProfiles": "Manipulation",
    "LootProfiles": "Loot",
    "ContainerTypes": "ContainerType",
    "AutopilotData": "Autopilot",
    "SecondaryAutopilotData": "Autopilot",
    "TertiaryAutopilotData": "Autopilot",
    "TargetData": "Target",
    "OverrideTargetData": "Target",
    "WeaponSystem": "Weapons",
    "Triggers": "Trigger",
    "TriggerGroups": "TriggerGroup",
    "Actions": "Action",
    "Conditions": "Condition",
    "ChatData": "Chat",
    "Spawner": "Spawn",
    "SpawnGroups": "SpawnGroup",
    "ResetTriggerCooldownNames": "Trigger",
    "EnableTriggerNames": "Trigger",
    "DisableTriggerNames": "Trigger",
    "ActivateTriggerNames": "Trigger",
}

# Faction tags the game itself provides.
BUILTIN_FACTIONS = {"SPRT", "Nobody"}

# Kinds that are expected to have no references (MES spawns them on its own).
ROOT_KINDS = {"SpawnGroup", "Faction"}

TAG_RE = re.compile(r"\[([A-Za-z]+):([^\]]*)\]")
HEADER_RE = re.compile(r"\[([^\]:]+)\]")


def header_kind(description):
    m = HEADER_RE.search(description or "")
    return HEADERS.get(m.group(1).strip()) if m else None


def main():
    root_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "Content")
    root_dir = os.path.normpath(root_dir)

    defs = {}       # name -> (kind, file)
    refs = []       # (expected kind, name, file, how)
    errors = []
    warnings = []
    frequencies = {}  # spawn group name -> <Frequency>
    spawner_groups = []  # (spawn group name, file) named by a RivalAI Spawn profile

    def define(name, kind, path):
        if name in defs:
            errors.append("%s: '%s' is defined twice (also in %s)" % (path, name, defs[name][1]))
        else:
            defs[name] = (kind, path)

    for dirpath, _, files in os.walk(root_dir):
        for fn in sorted(files):
            if not fn.lower().endswith(".sbc"):
                continue
            path = os.path.relpath(os.path.join(dirpath, fn))
            try:
                tree = ET.parse(path)
            except ET.ParseError as e:
                errors.append("%s: invalid XML: %s" % (path, e))
                continue

            for prefab in tree.getroot().iter("Prefab"):
                pid = prefab.find("Id")
                if pid is not None and pid.get("Subtype"):
                    define(pid.get("Subtype"), "Prefab", path)

            for faction in tree.getroot().iter("Faction"):
                if faction.get("Tag"):
                    define(faction.get("Tag"), "Faction", path)

            for ct in tree.getroot().iter("ContainerType"):
                sub = ct.findtext("Id/SubtypeId")
                if sub:
                    define(sub.strip(), "ContainerType", path)

            for kind_tag in ("EntityComponent", "SpawnGroup"):
                for el in tree.getroot().iter(kind_tag):
                    name = (el.findtext("Id/SubtypeId") or "").strip()
                    desc = el.findtext("Description") or ""
                    kind = header_kind(desc)
                    if not name or not kind:
                        continue
                    define(name, kind, path)
                    if kind_tag == "SpawnGroup":
                        try:
                            frequencies[name] = float((el.findtext("Frequency") or "0").strip())
                        except ValueError:
                            frequencies[name] = 0.0
                    for key, value in TAG_RE.findall(desc):
                        if key in REF_KEYS:
                            for v in value.split(","):
                                if v.strip():
                                    refs.append((REF_KEYS[key], v.strip(), path, "[%s:]" % key))
                                    if kind == "Spawn" and key == "SpawnGroups":
                                        spawner_groups.append((v.strip(), path))
                        elif key == "FactionOwner" and value.strip() not in BUILTIN_FACTIONS:
                            refs.append(("Faction", value.strip(), path, "[FactionOwner:]"))
                    if kind_tag == "SpawnGroup":
                        for p in el.iter("Prefab"):
                            if p.get("SubtypeId"):
                                refs.append(("Prefab", p.get("SubtypeId"), path, "<Prefab SubtypeId>"))
                            beh = (p.findtext("Behaviour") or "").strip()
                            if beh:
                                refs.append(("Behavior", beh, path, "<Behaviour>"))

    used = set()
    for kind, name, path, how in refs:
        used.add(name)
        if name not in defs:
            errors.append("%s: %s refers to '%s', which is not defined" % (path, how, name))
        elif defs[name][0] != kind:
            errors.append("%s: %s refers to '%s', which is a %s, not a %s (%s)"
                          % (path, how, name, defs[name][0], kind, defs[name][1]))

    for name, path in spawner_groups:
        if name in frequencies and frequencies[name] <= 0:
            errors.append("%s: [SpawnGroups:] names '%s', whose <Frequency> is 0, so MES will never spawn it"
                          % (path, name))

    for name, (kind, path) in sorted(defs.items(), key=lambda d: (d[1][1], d[0])):
        if name not in used and kind not in ROOT_KINDS:
            warnings.append("%s: %s '%s' is never referenced" % (path, kind, name))

    for w in warnings:
        print("warning: " + w)
    for e in errors:
        print("error: " + e)
    print("%d definitions, %d references, %d errors, %d warnings"
          % (len(defs), len(refs), len(errors), len(warnings)))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
