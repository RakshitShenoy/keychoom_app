#!/usr/bin/env python3
"""Authoring tool: regenerates the Floor 2-8 puzzles and room presets under app/content/.

Floor 1 ("floor1_cellar" and its f1_* puzzles) is hand-written and is never touched.
Every answer is computed by tools/puzzle_families.py, so rebuilding is deterministic and safe.

    python tools/build_content.py        # rewrite generated files
    python tools/validate_content.py     # then validate + bot-solve every preset

Room shape (the same for every generated preset):
    A  ---\\
           +--> C --> vault      A and B can be worked on IN PARALLEL by different teammates;
    B  ---/                      C needs the clue numbers revealed by BOTH. A trap object and
                                 sometimes an optional dare / a quiz object complete the room.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import puzzle_families as pf  # noqa: E402
from app.engines import challenge  # noqa: E402

CONTENT = Path(__file__).resolve().parent.parent / "app" / "content"
FLOOR_DIFFICULTY = challenge.FLOOR_DIFFICULTY
RUNES = {1: "W", 2: "I", 3: "T", 4: "C", 5: "H", 6: "E", 7: "R", 8: "Y"}
VAULT_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

# positions (x%, y%) for A, B, C, trap, extra, vault - a few layouts so rooms do not look identical
LAYOUTS = [
    [(12, 60), (50, 24), (68, 66), (30, 84), (88, 28), (86, 74)],
    [(20, 30), (78, 38), (44, 70), (10, 82), (60, 14), (88, 72)],
    [(16, 74), (36, 24), (74, 48), (54, 84), (90, 18), (84, 80)],
    [(10, 40), (62, 20), (40, 68), (88, 54), (24, 86), (82, 84)],
]

# fmt: off
THEMES = [
 dict(id="floor2_kitchen", floor=2, code="f2k", title="The Rotten Kitchen", vault="Meat Locker", quiz_slot=None, dare=True,
  intro="Flies drift over a feast that rotted a century ago. Something is still simmering. The way up is sealed in the meat locker at the back of the kitchen.",
  A=("Bubbling Cauldron", "🍲", "The broth settles. A number floats up in the grease: {v}."),
  B=("Rusted Cleaver", "🔪", "The blade is etched with a tally. The last mark is a number: {v}."),
  C=("Spice Rack", "🧂", "The jars rattle and a hidden drawer slides open. Inside, an old key is wrapped in butcher's paper."),
  trap=("Cracked Oven", "♨", "The oven door swings open and something inside SCREAMS."), extra=("Hungry Ghoul", "👹")),
 dict(id="floor2_pantry", floor=2, code="f2p", title="The Pantry of Whispers", vault="Larder Safe", quiz_slot="B", dare=False,
  intro="Shelves of dusty jars line the walls and every one of them seems to whisper. A small iron safe is bolted behind the biggest shelf.",
  A=("Pickling Jar", "🫙", "Something pale floats against the glass and its label reads: {v}."),
  B=("Whispering Barrel", "🛢", "The barrel stops whispering and a voice says one number: {v}."),
  C=("Flour Sack", "🌾", "The sack splits open and a brass key falls out of the flour."),
  trap=("Hanging Ham", "🍖", "The ham swings round and the face carved into it grins at you."), extra=("Mouse Hole", "🐭")),
 dict(id="floor3_library", floor=3, code="f3l", title="The Forbidden Library", vault="Locked Grimoire", quiz_slot="A", dare=False,
  intro="Books breathe on the shelves. A reading lamp burns without oil. At the centre of the room a chained grimoire waits for the right code.",
  A=("Chained Atlas", "📖", "A page falls open. A date is circled in red ink: {v}."),
  B=("Reading Lamp", "🏮", "The flame spells out a number in the smoke: {v}."),
  C=("Card Catalogue", "🗄", "A drawer slides out and a heavy iron key is lying where a card should be."),
  trap=("Whispering Bookcase", "📚", "The bookcase tilts and a face is pressed against the other side."), extra=("Librarian's Bell", "🔔")),
 dict(id="floor3_seance", floor=3, code="f3s", title="The Reading Room Seance", vault="Spirit Board Chest", quiz_slot=None, dare=False,
  intro="A round table, thirteen chairs and a single planchette that moves on its own. The chest beneath the table will only open for someone who has spoken to the dead.",
  A=("Spirit Board", "🔮", "The planchette slides to a number and stops: {v}."),
  B=("Crystal Ball", "🔮", "Inside the glass a mist forms the number {v}."),
  C=("Velvet Curtain", "🎭", "Behind the curtain, a small key hangs from a nail."),
  trap=("Empty Chair", "🪑", "The chair turns slowly towards you. It is not empty."), extra=("Tarot Deck", "🃏")),
 dict(id="floor4_nursery", floor=4, code="f4n", title="The Haunted Nursery", vault="Toy Chest", quiz_slot=None, dare=True,
  intro="A rocking horse creaks without a rider. A music box plays a lullaby that does not quite fit the tune. Whatever you need is locked inside the toy chest.",
  A=("Music Box", "🎵", "The lullaby stops on a single note and a voice whispers: {v}."),
  B=("Rocking Horse", "🐴", "A number is carved under the saddle: {v}."),
  C=("Doll House", "🏚", "In the tiny attic window a miniature key is hanging. It grows to full size in your hand."),
  trap=("Smiling Doll", "🪆", "The doll's head turns and its eyes open wide."), extra=("Spinning Top", "🪀")),
 dict(id="floor4_workshop", floor=4, code="f4w", title="The Dollmaker's Workshop", vault="Porcelain Cabinet", quiz_slot=None, dare=False,
  intro="Rows of unfinished faces watch you from the workbench. The dollmaker never left. The porcelain cabinet at the end of the room holds the way on.",
  A=("Glass Eyes", "👁", "One eye rolls to a stop. A number is painted on its back: {v}."),
  B=("Wooden Limbs", "🦴", "Stamped on a limb: batch number {v}."),
  C=("Sewing Machine", "🧵", "The needle stops and a cabinet key falls out of the bobbin."),
  trap=("Half-Made Doll", "🧸", "The half-made doll sits up and screams."), extra=("Pin Cushion", "📌")),
 dict(id="floor5_ballroom", floor=5, code="f5b", title="The Phantom Ballroom", vault="Orchestra Pit Vault", quiz_slot=None, dare=False,
  intro="A waltz plays from nowhere. Dancers you cannot see sweep past you in the dust. The orchestra pit hides a vault that has been waiting for the last dance.",
  A=("Grand Piano", "🎹", "A single key sinks down on its own. Under it a number is engraved: {v}."),
  B=("Chandelier", "🕯", "A crystal drops and shatters. In the fragments you read the number {v}."),
  C=("Gramophone", "📻", "The record slows and a voice says where the key is hidden. You find it inside the horn."),
  trap=("Dancing Mirror", "🪞", "Your reflection does not copy you. It lunges."), extra=("Masquerade Mask", "🎭")),
 dict(id="floor5_gallery", floor=5, code="f5g", title="The Portrait Gallery", vault="Curator's Vault", quiz_slot=None, dare=True,
  intro="Hundreds of portraits line the walls and every pair of eyes follows you. The curator's vault is hidden behind the largest frame.",
  A=("Founder's Portrait", "🖼", "Behind the canvas someone wrote a number: {v}."),
  B=("Marble Bust", "🗿", "The plinth is engraved with a number: {v}."),
  C=("Gilded Frame", "🪞", "The frame pivots outward and a key drops into your hand."),
  trap=("Weeping Portrait", "😭", "The painted face opens its mouth and shrieks."), extra=("Velvet Rope", "🪢")),
 dict(id="floor6_chapel", floor=6, code="f6c", title="The Ruined Chapel", vault="Reliquary", quiz_slot=None, dare=True,
  intro="Rain falls through a broken roof onto pews that have not held a congregation in centuries. The altar's reliquary is the next lock.",
  A=("Stained Glass", "🪟", "Light through the glass paints a number on the floor: {v}."),
  B=("Church Bell", "🔔", "The bell tolls and a number is cast into the bronze: {v}."),
  C=("Altar Candles", "🕯", "The flames lean towards the altar. Under the cloth lies a small key."),
  trap=("Confessional", "🚪", "The confessional door bursts open. Something was listening."), extra=("Prayer Book", "📕")),
 dict(id="floor6_crypt", floor=6, code="f6k", title="The Crypt Below the Altar", vault="Sarcophagus", quiz_slot="B", dare=False,
  intro="Stone steps lead down into cold air that smells of earth. Coffins line the walls. One sarcophagus has a lock instead of a lid.",
  A=("Bone Pile", "💀", "Among the bones a rib has a number scratched into it: {v}."),
  B=("Iron Gate", "⛓", "The gate rattles. A number is hammered into the rusted bars: {v}."),
  C=("Stone Angel", "😇", "The angel's hand opens and a stone key tumbles out."),
  trap=("Open Coffin", "⚰", "The coffin is not empty. It never was."), extra=("Skull Candle", "💀")),
 dict(id="floor7_attic", floor=7, code="f7a", title="The Witch's Attic", vault="Hexed Trunk", quiz_slot=None, dare=False,
  intro="Bundles of herbs hang from the rafters. Spiders have woven a second ceiling. A trunk wrapped in chains sits under the one window of the attic.",
  A=("Hanging Herbs", "🌿", "One bundle is tied with a thread knotted into a number: {v}."),
  B=("Old Broomstick", "🧹", "The bristles spell out a word in the dust: {v}."),
  C=("Black Cat Statue", "🐈‍⬛", "The cat's eyes flash green. A key is tied around its neck."),
  trap=("Spider Web", "🕸", "The web shivers. Something very large drops from the rafters."), extra=("Potion Shelf", "⚗")),
 dict(id="floor7_clocktower", floor=7, code="f7c", title="The Clocktower", vault="Mechanism Box", quiz_slot=None, dare=True,
  intro="Giant gears grind in the dark. The clock has been striking thirteen for a hundred years. The mechanism box in the middle of the gears needs the right code.",
  A=("Brass Gear", "⚙", "A tooth count is stamped on the gear: {v}."),
  B=("Pendulum", "⏳", "The pendulum swings out a word in the dust: {v}."),
  C=("Clock Face", "🕰", "The hands spin and stop. A panel opens behind the clock face to show a key."),
  trap=("Cuckoo Door", "🐦", "The tiny door bursts open and a shrieking face leaps out."), extra=("Bell Rope", "🔔")),
 dict(id="floor8_study", floor=8, code="f8s", title="The Demon's Study", vault="Pentagram Safe", quiz_slot=None, dare=False,
  intro="The last room before the final door. Black candles burn in a ring. A demon's contracts cover the desk and the safe under the pentagram holds the last fragment.",
  A=("Contract Scroll", "📜", "One clause is underlined in blood. It contains the number {v}."),
  B=("Obsidian Skull", "💀", "Its jaw opens and a hollow voice says one word: {v}."),
  C=("Devil's Desk", "🪑", "A drawer opens that was not there before. Inside lies the final key."),
  trap=("Black Mirror", "🪞", "The black mirror ripples and a face rushes toward you."), extra=("Ritual Dagger", "🗡")),
 dict(id="floor8_sanctum", floor=8, code="f8z", title="The Final Sanctum", vault="Heart Vault", quiz_slot=None, dare=True,
  intro="The walls pulse like something alive. A heart beats under the floor. Whatever lives here will not let you leave easily, and the Heart Vault is the last lock before the final door.",
  A=("Beating Heart Stone", "🫀", "It beats out a number in the stone: {v}."),
  B=("Chained Throne", "👑", "A word is carved into the arm of the throne: {v}."),
  C=("Bone Altar", "🦴", "The bones rearrange themselves and form an arrow pointing to a hidden key."),
  trap=("Screaming Wall", "🧱", "A face pushes out of the wall and screams."), extra=("Whispering Torch", "🔥")),
]
# fmt: on


def reveal_text(tpl: str, value) -> str:
    return tpl.format(v=value)


def build_theme(theme: dict, used_families: dict) -> tuple[dict, dict]:
    rng = random.Random(theme["id"])
    floor, diff = theme["floor"], FLOOR_DIFFICULTY[theme["floor"]]
    layout = LAYOUTS[sum(map(ord, theme["id"])) % len(LAYOUTS)]
    code = "".join(rng.choice(VAULT_ALPHABET) for _ in range(4))
    # mix letters and digits so the code looks like a code
    if code.isalpha() or code.isdigit():
        code = code[0] + rng.choice("23456789") + code[2] + rng.choice("ABCDEFGHJKMNPQRSTUVWXYZ")
    comb = rng.choice(pf.COMBINERS[diff])
    # clue values the independent objects reveal
    if comb is pf.comb_crt:
        a, b = rng.choice([5, 7]), rng.choice([3, 4, 9])
    elif comb is pf.comb_paths:
        a, b = rng.randint(3, 6), rng.randint(3, 6)
    elif comb is pf.comb_vigenere:
        a, b = rng.randint(2, 5), rng.choice(pf.KEYS)
    else:
        a, b = rng.randint(3, 9), rng.randint(3, 9)

    objects, puzzles, hints = [], {}, {}
    vault_pos = {"A": [0], "B": [1], "C": [2, 3]}
    code_chars = lambda role: "".join(code[i] for i in vault_pos[role])

    def vault_note(role):
        pos = vault_pos[role]
        if len(pos) == 1:
            return f" The vault's character number {pos[0] + 1} is {code[pos[0]]}."
        return f" The vault's characters number {pos[0] + 1} and {pos[1] + 1} are {code[pos[0]]} and {code[pos[1]]}."

    def pick_independent(taken):
        options = [f for f in pf.INDEPENDENT[diff] if f not in taken and used_families.get((floor, f), 0) == 0] \
            or [f for f in pf.INDEPENDENT[diff] if f not in taken]
        return rng.choice(options)

    taken = []
    for slot, (role, value) in enumerate((("A", a), ("B", b))):
        label, icon, tpl = theme[role]
        x, y = layout[slot]
        oid = {"A": "a_" + label.split()[-1].lower(), "B": "b_" + label.split()[-1].lower()}[role]
        oid = "".join(ch for ch in oid if ch.isalnum() or ch == "_")
        if theme["quiz_slot"] == role:
            obj = {"id": oid, "type": "quiz", "label": label, "icon": icon, "position": {"x": x, "y": y},
                   "requires": [], "vault_positions": vault_pos[role],
                   "quiz": {"pool": "core_reasoning", "questions": 5, "time_limit_seconds": 150, "pass_score": 3},
                   "reward_text": reveal_text(tpl, value)}
            hints[oid] = [
                "This is a quick quiz of five reasoning questions. Read each one calmly.",
                "You need 3 of the 5 right. The server keeps the clock, so do not dawdle.",
                "If you fail you can try again with a fresh set of questions.",
                "Answer the five questions and the reward shows a clue number." + vault_note(role),
            ]
        else:
            fam = pick_independent(taken)
            taken.append(fam)
            used_families[(floor, fam)] = used_families.get((floor, fam), 0) + 1
            made = fam(rng, diff)
            pid = f"{theme['code']}_{role.lower()}"
            puzzles[pid] = {"id": pid, "title": made.title, "category": made.category, "difficulty": diff,
                            "steps": made.steps, "reveal_text": reveal_text(tpl, value)}
            obj = {"id": oid, "type": "puzzle", "label": label, "icon": icon, "position": {"x": x, "y": y},
                   "puzzle": pid, "requires": [], "vault_positions": vault_pos[role]}
            hints[oid] = made.hints[:3] + [made.hints[3] + vault_note(role)]
        objects.append(obj)

    label, icon, tpl = theme["C"]
    x, y = layout[2]
    cid = "c_" + "".join(ch for ch in label.split()[-1].lower() if ch.isalnum())
    made = comb(rng, diff, a, b, theme["A"][0], theme["B"][0])
    pid = f"{theme['code']}_c"
    puzzles[pid] = {"id": pid, "title": made.title, "category": made.category, "difficulty": diff,
                    "steps": made.steps, "reveal_text": tpl}
    objects.append({"id": cid, "type": "puzzle", "label": label, "icon": icon, "position": {"x": x, "y": y},
                    "puzzle": pid, "requires": [objects[0]["id"], objects[1]["id"]], "vault_positions": vault_pos["C"]})
    hints[cid] = made.hints[:3] + [made.hints[3] + vault_note("C")]

    tl, ti, tt = theme["trap"]
    tx, ty = layout[3]
    objects.append({"id": "trap_" + "".join(ch for ch in tl.split()[-1].lower() if ch.isalnum()), "type": "trap", "label": tl,
                    "icon": ti, "position": {"x": tx, "y": ty}, "requires": [], "reward_text": tt})
    if theme["dare"]:
        el, ei = theme["extra"]
        ex, ey = layout[4]
        did = "dare_" + "".join(ch for ch in el.split()[-1].lower() if ch.isalnum())
        objects.append({"id": did, "type": "dare", "label": el, "icon": ei, "position": {"x": ex, "y": ey},
                        "requires": [], "dare_pool": "core_dares",
                        "reward_text": "The spirit is amused. It whispers: 'Everything in this room is connected. Finish what you started.'"})
        hints[did] = ["A dare is optional and never needed to leave the floor.", "It never costs you anything to skip it.",
                      "Do the dare only if you and your team are happy with it.", "It never owns part of the vault code. Skip it freely."]

    order = [o["id"] for o in objects[:3]]
    hints["vault"] = [
        "The code has four characters. Some are letters, some are digits.",
        "Each solved object revealed part of the code. Check the vault display.",
        f"The pieces go in the order they sit on the vault: {', '.join(o['label'] for o in objects[:3])}.",
        f"The code reads {code}.",
    ]
    for o in objects:
        if o["type"] == "trap":
            continue
    vx, vy = layout[5]
    preset = {
        "id": theme["id"], "floor": floor, "title": theme["title"], "intro": theme["intro"],
        "fragment": {"id": f"fragment_{floor}", "label": f"Floor {floor} Key Fragment", "rune": RUNES[floor]},
        "vault": {"label": theme["vault"], "length": 4, "code": code, "requires": [cid], "position": {"x": vx, "y": vy}},
        "objects": objects, "hints": hints,
    }
    return preset, puzzles


def main() -> None:
    for old in (CONTENT / "puzzles").glob("f[2-8]*.json"):
        old.unlink()
    for old in (CONTENT / "presets").glob("floor[2-8]_*.json"):
        old.unlink()
    used: dict = {}
    count = 0
    for theme in THEMES:
        preset, puzzles = build_theme(theme, used)
        for pid, puzzle in puzzles.items():
            errs = challenge.validate_puzzle(puzzle)
            assert not errs, errs
            (CONTENT / "puzzles" / f"{pid}.json").write_text(json.dumps(puzzle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            count += 1
        (CONTENT / "presets" / f"{preset['id']}.json").write_text(json.dumps(preset, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(THEMES)} presets and {count} puzzles")


if __name__ == "__main__":
    main()
