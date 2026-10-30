"""Puzzle families used by tools/build_content.py.

Every family is a function ``(rng, difficulty, ...) -> Made`` that builds a puzzle whose answer is
COMPUTED (and, for deduction puzzles, proven unique by brute force) - nothing is typed in by hand, so a
generated room cannot contain a wrong or ambiguous answer. This is an authoring tool: the shipped game
only reads the JSON files it writes.
"""
from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass
class Made:
    category: str
    title: str
    steps: list[dict[str, Any]]          # one entry per answerable step
    hints: list[str]                     # 4 hints, vague -> explicit
    meta: dict[str, Any] = field(default_factory=dict)  # e.g. {"answer": ...} for chaining


def step(prompt, answer_type, answer, *, clues=None, table=None, placeholder="", answers=None,
         tolerance=0, wrong=None, ok=None) -> dict[str, Any]:
    s: dict[str, Any] = {"prompt": prompt, "answer_type": answer_type, "answer": answer}
    if clues:
        s["clues"] = list(clues)
    if table:
        s["table"] = table
    if placeholder:
        s["placeholder"] = placeholder
    if answers:
        s["answers"] = list(answers)
    if tolerance:
        s["tolerance"] = tolerance
    if wrong:
        s["wrong_feedback"] = wrong
    if ok:
        s["success_message"] = ok
    return s


def ordinal(n: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


def caesar(text: str, shift: int) -> str:
    return "".join(ALPHA[(ALPHA.index(c) + shift) % 26] if c in ALPHA else c for c in text)


def vigenere_enc(text: str, key: str) -> str:
    return "".join(ALPHA[(ALPHA.index(c) + ALPHA.index(key[i % len(key)])) % 26] for i, c in enumerate(text))


def vigenere_dec(text: str, key: str) -> str:
    return "".join(ALPHA[(ALPHA.index(c) - ALPHA.index(key[i % len(key)])) % 26] for i, c in enumerate(text))


WORDS = ["COBWEB", "LANTERN", "SPECTER", "PUMPKIN", "GRAVEYARD", "WARLOCK", "CANDLE", "SKELETON", "BROOMSTICK",
         "CAULDRON", "TOMBSTONE", "VAMPIRE", "WEREWOLF", "MIDNIGHT", "NIGHTMARE", "POLTERGEIST", "ENCHANTED",
         "SHADOW", "BANSHEE", "COFFIN", "MUMMY", "GARGOYLE", "RAVEN", "PHANTOM", "WRAITH", "HAUNTED"]
KEYS = ["BONE", "MOON", "WEB", "CRYPT", "RAVEN", "GRIM", "OMEN", "ASH", "EMBER", "DUSK", "HEX", "TOMB"]


# =====================================================================================
# sequences
# =====================================================================================
def seq_arith_diff(rng, diff):
    start, d0, inc = rng.randint(2, 9), rng.randint(1, 4), rng.randint(1, 3)
    terms, d = [start], d0
    for _ in range(5):
        terms.append(terms[-1] + d)
        d += inc
    ans = terms[-1] + d
    return Made("sequences", "The Scratched Numbers", [step(
        "Numbers are scratched into the plaster in a row: " + ", ".join(map(str, terms)) + ", ... and then a blank. What number belongs in the blank?",
        "numeric", ans, placeholder="the next number")], [
        "Do not stare at the numbers themselves. Look at the gaps between neighbours.",
        f"The gaps are {', '.join(str(terms[i+1]-terms[i]) for i in range(5))}. Do the gaps follow a rule?",
        f"The gaps grow by {inc} each time, so the next gap is {d}.",
        f"Add {d} to {terms[-1]}. The answer is {ans}."])


def seq_geo_diff(rng, diff):
    start, d0, ratio = rng.randint(2, 7), rng.randint(1, 2), rng.choice([2, 3])
    terms, d = [start], d0
    for _ in range(4):
        terms.append(terms[-1] + d)
        d *= ratio
    ans = terms[-1] + d
    return Made("sequences", "The Doubling Gaps", [step(
        "A dial's numbers are carved around its rim: " + ", ".join(map(str, terms)) + ", ... What number comes next?",
        "numeric", ans, placeholder="the next number")], [
        "Look at the differences between neighbouring numbers.",
        f"The differences are {', '.join(str(terms[i+1]-terms[i]) for i in range(4))}. Each is a multiple of the one before.",
        f"The differences are multiplied by {ratio} each time, so the next difference is {d}.",
        f"{terms[-1]} + {d} = {ans}."])


def seq_interleave(rng, diff):
    a0, da = rng.randint(2, 9), rng.randint(2, 5)
    b0, rb = rng.randint(1, 3), rng.choice([2, 3])
    n = 9
    terms = []
    for i in range(n):
        terms.append(a0 + da * (i // 2) if i % 2 == 0 else b0 * rb ** (i // 2))
    ask = 11  # 11th term (odd index -> the geometric strand)
    ans = b0 * rb ** ((ask - 1) // 2) if ask % 2 == 0 else a0 + da * ((ask - 1) // 2)
    return Made("patterns", "Two Voices in One Song", [step(
        "Two sequences were woven together, taking turns. The row reads: " + ", ".join(map(str, terms)) + ", ... What is the 11th number in the row?",
        "numeric", ans, placeholder="the 11th number")], [
        "Try separating the numbers in odd positions from the numbers in even positions.",
        f"Odd positions: {', '.join(str(terms[i]) for i in range(0, n, 2))}. Even positions: {', '.join(str(terms[i]) for i in range(1, n, 2))}.",
        f"The 11th number is in the odd-position strand, which adds {da} each time (it is the 6th number of that strand).",
        f"{a0} + {da} x 5 = {ans}."])


def seq_polynomial(rng, diff):
    a, b, c = rng.randint(1, 3), rng.randint(0, 5), rng.randint(1, 6)
    f = lambda n: a * n * n + b * n + c
    shown = [f(n) for n in range(1, 7)]
    n_ask = 12
    return Made("sequences", "The Tolling Bell", [step(
        "The clock tower tolls a growing number of times each hour: " + ", ".join(map(str, shown)) + " ... How many tolls will it make at the 12th hour?",
        "numeric", f(n_ask), placeholder="number of tolls")], [
        "Take the differences between neighbours. Then take the differences of those differences.",
        f"First differences: {', '.join(str(shown[i+1]-shown[i]) for i in range(5))}. The second differences are all the same: {2*a}.",
        f"A constant second difference of {2*a} means the rule has the form {a}n^2 + bn + c. Use the first terms to find b and c.",
        f"The rule is {a}n^2 + {b}n + {c}. For n = 12 that is {f(n_ask)}."])


def seq_fib_like(rng, diff):
    a, b, c = rng.randint(1, 4), rng.randint(2, 5), rng.randint(1, 3)
    t = [a, b]
    for _ in range(6):
        t.append(t[-1] + t[-2] + c)
    ask = 10
    full = list(t)
    while len(full) < ask:
        full.append(full[-1] + full[-2] + c)
    return Made("sequences", "The Growing Brood", [step(
        f"Each generation of bats is the sum of the two before it, plus {c} newcomers. The first generations are {', '.join(map(str, t))} ... How many bats are in generation {ask}?",
        "numeric", full[ask - 1], placeholder="number of bats")], [
        "Write the rule as a formula: next = previous + the one before that + a fixed extra.",
        f"Check it: {t[0]} + {t[1]} + {c} = {t[2]}. Keep going one generation at a time.",
        f"Generations 7, 8 and 9 are {full[6]}, {full[7]}, {full[8]}.",
        f"Generation {ask} = {full[8]} + {full[7]} + {c} = {full[9]}."])


def seq_recurrence(rng, diff):
    a, b = rng.randint(1, 4), rng.randint(2, 6)
    t = [a, b]
    for n in range(3, 11):
        t.append(2 * t[-1] - t[-2] + n)
    return Made("sequences", "The Cursed Recurrence", [step(
        f"A cursed ledger defines T(1) = {a}, T(2) = {b}, and for every n from 3 onwards T(n) = 2 x T(n-1) - T(n-2) + n. What is T(10)?",
        "numeric", t[9], placeholder="T(10)")], [
        "You must compute the terms one by one. Start from T(3).",
        f"T(3) = 2 x {b} - {a} + 3 = {t[2]}. T(4) = 2 x {t[2]} - {b} + 4 = {t[3]}.",
        f"T(5) = {t[4]}, T(6) = {t[5]}, T(7) = {t[6]}, T(8) = {t[7]}.",
        f"T(9) = {t[8]} and T(10) = {t[9]}."])


# =====================================================================================
# ciphers
# =====================================================================================
def cipher_caesar(rng, diff):
    word = rng.choice(WORDS)
    shift = rng.randint(3, 23)
    return Made("cryptic", "The Shifted Whisper", [step(
        f"A note is written in a simple code. Every letter was moved {shift} places FORWARD in the alphabet (after Z comes A). The note reads: {caesar(word, shift)}. What was the original word?",
        "text", word.lower(), placeholder="the original word")], [
        "To decode, move every letter BACKWARD by the same number of places.",
        f"Move each letter back {shift}. The first letter {caesar(word, shift)[0]} becomes ...",
        f"The first letter of the answer is {word[0]} and the word has {len(word)} letters.",
        f"The word is {word}."])


def cipher_crib(rng, diff):
    word = rng.choice(WORDS)
    crib = rng.choice([w for w in ["GHOST", "BONES", "CRYPT", "WITCH", "SKULL"]])
    shift = rng.randint(3, 23)
    return Made("cryptic", "The Matching Cipher", [step(
        f"A second letter was sealed with the same secret shift as an earlier one. You are told that the shift turns {crib} into {caesar(crib, shift)}. Decode the new message: {caesar(word, shift)}.",
        "text", word.lower(), placeholder="the hidden word")], [
        f"Compare {crib[0]} with {caesar(crib, shift)[0]}: how many places forward is that?",
        f"The shift is {shift} places forward, so decode by moving {shift} places backward.",
        f"The message starts with {word[0]} and has {len(word)} letters.",
        f"The word is {word}."])


# =====================================================================================
# word problems (all integer answers, computed with exact fractions)
# =====================================================================================
def word_rate(rng, diff):
    kind = rng.choice(["taps", "painters", "ages", "discount", "meet"])
    if kind == "taps":
        a, b = rng.choice([(12, 6), (10, 15), (6, 3), (20, 5), (8, 8), (30, 20)])
        ans = Fraction(1, 1) / (Fraction(1, a) + Fraction(1, b))
        return Made("mathematics", "The Two Taps", [step(
            f"Two taps feed a leaking cauldron. Alone, the first fills it in {a} hours and the second in {b} hours. With both open at once, how many hours does it take to fill?",
            "numeric", str(ans), placeholder="hours")], [
            "Think in terms of how much of the cauldron each tap fills in ONE hour.",
            f"The taps fill 1/{a} and 1/{b} of the cauldron per hour.",
            f"Together: 1/{a} + 1/{b} = {Fraction(1,a)+Fraction(1,b)} of the cauldron per hour.",
            f"Time = 1 / ({Fraction(1,a)+Fraction(1,b)}) = {ans} hours."])
    if kind == "painters":
        a, b = rng.choice([(6, 3), (10, 15), (12, 4), (20, 30)])
        ans = Fraction(1, 1) / (Fraction(1, a) + Fraction(1, b))
        return Made("mathematics", "The Ghostly Painters", [step(
            f"One ghost paints the haunted fence in {a} hours, another in {b} hours. Working side by side, how many hours do they need?",
            "numeric", str(ans), placeholder="hours")], [
            "Work out the share of the fence each ghost paints per hour.",
            f"Rates: 1/{a} and 1/{b} of the fence per hour.",
            f"Combined rate = {Fraction(1,a)+Fraction(1,b)} of the fence per hour.",
            f"Time = {ans} hours."])
    if kind == "ages":
        child = rng.randint(6, 14)
        k = rng.choice([3, 4])
        n = rng.randint(2, 8)
        # father = k * child now; in n years father = (k-1)*child' ... solve: father + n = m*(child+n)
        m = 2
        # choose so that integer: k*c + n = 2*(c + n)  -> c = n/(k-2) ; build backwards
        c = rng.randint(4, 12)
        n = (k - 2) * c
        father = k * c
        assert father + n == 2 * (c + n)
        return Made("mathematics", "The Ageless Butler", [step(
            f"The butler is {k} times as old as the maid. In {n} years he will be exactly twice as old as she will be then. How old is the butler now?",
            "numeric", father, placeholder="years")], [
            "Let the maid's age be x. The butler's age is a multiple of x.",
            f"Now: butler = {k}x. In {n} years: butler = {k}x + {n}, maid = x + {n}.",
            f"Solve {k}x + {n} = 2(x + {n}).",
            f"x = {c}, so the butler is {father}."])
    if kind == "discount":
        base = rng.choice([200, 250, 400, 500, 800])
        up, down = rng.choice([(20, 25), (50, 20), (25, 20), (10, 10), (40, 25)])
        ans = Fraction(base) * Fraction(100 + up, 100) * Fraction(100 - down, 100)
        return Made("mathematics", "The Cursed Price Tag", [step(
            f"A cursed lantern costs {base} coins. The seller raises the price by {up}%, then, taking pity, cuts the NEW price by {down}%. What is the final price in coins?",
            "numeric", str(ans), placeholder="coins")], [
            "Percentage changes are applied one after the other, each to the latest price.",
            f"After the rise: {base} x {Fraction(100+up,100)} = {Fraction(base)*Fraction(100+up,100)}.",
            f"Now take {down}% off that amount.",
            f"The final price is {ans} coins."])
    d1, d2 = rng.choice([(180, 60), (240, 40), (150, 25), (210, 30)])
    s1, s2 = rng.choice([(40, 20), (30, 15), (50, 25)])
    # two ghosts walk toward each other
    t = Fraction(d1, s1 + s2)
    ans = t * s1
    return Made("mathematics", "The Corridor Meeting", [step(
        f"Two ghosts glide toward each other along a {d1} m corridor, one at {s1} m per minute and the other at {s2} m per minute. How many metres from the first ghost's start do they meet?",
        "numeric", str(ans), placeholder="metres")], [
        "They close the gap together. Find out how long until they meet.",
        f"Their speeds add up: {s1} + {s2} = {s1+s2} m per minute.",
        f"Time to meet = {d1} / {s1+s2} = {t} minutes.",
        f"Distance for the first ghost = {s1} x {t} = {ans} metres."])


# =====================================================================================
# data interpretation
# =====================================================================================
def table_stat(rng, diff):
    kind = rng.choice(["weighted", "mean_missing", "median"])
    rooms = ["Cellar", "Kitchen", "Library", "Nursery", "Chapel", "Attic", "Ballroom", "Study"]
    if kind == "weighted":
        rng.shuffle(rooms)
        rows = [(rooms[i], rng.randint(2, 9), rng.randint(2, 6)) for i in range(4)]
        ans = sum(g * p for _, g, p in rows)
        return Made("data_interpretation", "The Ghost Ledger", [step(
            "The ledger lists, for each room, how many ghosts live there and the coins each ghost haunts away per night. How many coins are haunted away in total each night?",
            "numeric", ans, table={"columns": ["Room", "Ghosts", "Coins per ghost"], "rows": [[r, g, p] for r, g, p in rows]},
            placeholder="coins per night")], [
            "Each room's total is ghosts x coins per ghost.",
            "Work out the four room totals first.",
            "Room totals: " + ", ".join(str(g * p) for _, g, p in rows) + ".",
            f"Add them: {ans}."])
    if kind == "mean_missing":
        vals = [rng.randint(3, 9) for _ in range(5)]
        mean = rng.randint(5, 8)
        missing = mean * 6 - sum(vals)
        if not 1 <= missing <= 20:
            vals = [mean] * 5
            vals[0] += 2
            vals[1] -= 2
            missing = mean * 6 - sum(vals)
        return Made("statistics", "The Missing Night", [step(
            f"A watchman counted the knocks heard on six nights. Five nights are in the table; the sixth smudged away. The mean over all six nights was exactly {mean}. How many knocks were there on night 6?",
            "numeric", missing, table={"columns": ["Night", "1", "2", "3", "4", "5"], "rows": [["Knocks"] + [str(v) for v in vals]]},
            placeholder="knocks on night 6")], [
            "If you know the mean and the number of nights, you know the total.",
            f"Total over six nights = {mean} x 6 = {mean * 6}.",
            f"The five known nights add up to {sum(vals)}.",
            f"{mean * 6} - {sum(vals)} = {missing}."])
    vals = sorted(rng.sample(range(3, 40), 7))
    shown = vals[:]
    rng.shuffle(shown)
    med = vals[3]
    return Made("statistics", "The Middle Gravestone", [step(
        "Seven gravestones stand in a muddled row. Their ages, in the order you find them, are: " + ", ".join(map(str, shown)) + ". What is the MEDIAN age?",
        "numeric", med, placeholder="median age")], [
        "The median is the middle value once the numbers are sorted.",
        "Put the ages in order from smallest to largest.",
        "Sorted: " + ", ".join(map(str, vals)) + ".",
        f"With 7 numbers the median is the 4th one: {med}."])


# =====================================================================================
# probability / counting / number systems
# =====================================================================================
def probability(rng, diff):
    r, b, g = rng.randint(2, 5), rng.randint(2, 5), rng.randint(0, 4)
    n = r + b + g
    kind = rng.choice(["same", "diff", "red"])
    pairs = list(itertools.combinations(range(n), 2))
    colours = ["red"] * r + ["black"] * b + ["green"] * g

    def ok(p):
        c1, c2 = colours[p[0]], colours[p[1]]
        return c1 == c2 if kind == "same" else (c1 != c2 if kind == "diff" else "red" in (c1, c2))

    ans = Fraction(sum(1 for p in pairs if ok(p)), len(pairs))
    what = {"same": "the same colour", "diff": "two different colours", "red": "at least one red stone"}[kind]
    stones = f"{r} red, {b} black" + (f" and {g} green" if g else "")
    return Made("probability", "The Urn of Souls", [step(
        f"An urn holds {stones} soul-stones. Two stones are drawn one after the other WITHOUT putting the first back. What is the probability that they are {what}? Give a fraction (like 3/10) or a decimal.",
        "numeric", f"{ans.numerator}/{ans.denominator}", placeholder="e.g. 3/10")], [
        f"There are {n} stones, so the number of ways to pick any pair is {n} x {n-1} / 2 = {len(pairs)}.",
        "Count the pairs that give the wanted outcome, then divide by the total number of pairs.",
        f"Wanted pairs: {sum(1 for p in pairs if ok(p))} out of {len(pairs)}.",
        f"The probability is {ans.numerator}/{ans.denominator}."])


def counting(rng, diff):
    word, rule = rng.choice([
        ("SPOOKY", "no_adjacent_o"), ("GHOULS", "ends_consonant"), ("WITCHES", "vowels_apart"),
        ("MUMMY", "no_adjacent_m"), ("COFFINS", "ends_consonant"), ("BANSHEE", "no_adjacent_e"),
    ])
    perms = set(itertools.permutations(word))
    vowels = set("AEIOUY") if rule != "vowels_apart" else set("AEIOU")

    def good(p):
        s = "".join(p)
        if rule == "no_adjacent_o":
            return "OO" not in s
        if rule == "no_adjacent_m":
            return "MM" not in s
        if rule == "no_adjacent_e":
            return "EE" not in s
        if rule == "ends_consonant":
            return s[0] not in "AEIOU" and s[-1] not in "AEIOU"
        return all(not (s[i] in vowels and s[i + 1] in vowels) for i in range(len(s) - 1))

    ans = sum(1 for p in perms if good(p))
    text = {
        "no_adjacent_o": "no two O's next to each other",
        "no_adjacent_m": "no two M's next to each other",
        "no_adjacent_e": "no two E's next to each other",
        "ends_consonant": "a consonant at BOTH ends (vowels are A, E, I, O, U)",
        "vowels_apart": "no two vowels (A, E, I, O, U) next to each other",
    }[rule]
    total = len(perms)
    return Made("mathematics", "The Scrambled Epitaph", [step(
        f"The letters of the word {word} are carved on separate tiles. How many DIFFERENT arrangements of all the tiles have {text}? (Identical letters are indistinguishable.)",
        "numeric", ans, placeholder="number of arrangements")], [
        f"First count ALL different arrangements of the tiles. Repeated letters divide the count.",
        f"There are {total} different arrangements in total.",
        "Count the arrangements that break the rule, then subtract (or count the good ones directly).",
        f"The answer is {ans}."])


def base_conv(rng, diff):
    base = rng.choice([3, 4, 5, 6, 7])
    value = rng.randint(60, 400)
    digits = []
    v = value
    while v:
        digits.append(str(v % base))
        v //= base
    rep = "".join(reversed(digits))
    return Made("numerical_reasoning", "The Strange Numerals", [step(
        f"The tomb door shows the number {rep}, written in base {base} (only the digits 0 to {base-1} appear). What number is that in ordinary base 10?",
        "numeric", value, placeholder="in base 10")], [
        f"In base {base}, each place is worth {base} times the place to its right.",
        f"From the right the places are worth 1, {base}, {base**2}, {base**3}, ...",
        "Multiply each digit by its place value and add.",
        f"The answer is {value}."])


def grid_paths(rng, diff):
    n = 6 if diff in ("hard", "very_hard") else 5
    while True:
        blocked = set(rng.sample([(r, c) for r in range(n) for c in range(n) if (r, c) not in ((0, 0), (n - 1, n - 1))], n - 1))
        dp = [[0] * n for _ in range(n)]
        dp[0][0] = 1
        for r in range(n):
            for c in range(n):
                if (r, c) in blocked or (r, c) == (0, 0):
                    continue
                dp[r][c] = (dp[r - 1][c] if r else 0) + (dp[r][c - 1] if c else 0)
        if dp[n - 1][n - 1] >= 3:
            break
    rows = [["#" if (r, c) in blocked else "." for c in range(n)] for r in range(n)]
    return Made("logic", "The Shifting Floor", [step(
        f"The floor is a {n} x {n} grid of tiles. A '#' tile has collapsed and cannot be stepped on. Starting on the top-left tile, you may only step RIGHT or DOWN. How many different routes lead to the bottom-right tile?",
        "numeric", dp[n - 1][n - 1], table={"columns": [str(i + 1) for i in range(n)], "rows": rows}, placeholder="number of routes")], [
        "Count routes tile by tile: the routes to a tile = routes to the tile above + routes to the tile on its left.",
        "A collapsed tile has 0 routes. Start with the top-left tile = 1 and fill the grid row by row.",
        "Bottom row, built up: " + ", ".join(str(dp[n - 1][c]) for c in range(n)) + ".",
        f"The answer is {dp[n - 1][n - 1]}."])


# =====================================================================================
# deduction (brute-force verified)
# =====================================================================================
SURNAMES = ["Baron", "Maid", "Butler", "Widow", "Doctor", "Priest", "Cook", "Heir", "Duke"]
NOUNS = {"frame": "frame", "pew": "pew", "chair": "chair", "stall": "stall"}


def _clue_text(c, noun):
    k = c[0]
    if k == "left":
        return f"The {c[1]} is somewhere to the left of the {c[2]}."
    if k == "adj":
        return f"The {c[1]} is directly next to the {c[2]}."
    if k == "notadj":
        return f"The {c[1]} is NOT next to the {c[2]}."
    if k == "end":
        return f"The {c[1]} is at one of the two ends of the row."
    if k == "between":
        return f"The {c[1]} is somewhere between the {c[2]} and the {c[3]}."
    if k == "gap":
        return f"Exactly {c[3]} {noun}{'s' if c[3] != 1 else ''} separate the {c[1]} from the {c[2]}."
    if k == "notpos":
        return f"The {c[1]} is not in {noun} {c[2]}."
    if k == "parity":
        return f"The {c[1]} is in an {'odd' if c[2] else 'even'}-numbered {noun}."
    raise ValueError(k)


def _clue_true(c, pos):
    k = c[0]
    if k == "left":
        return pos[c[1]] < pos[c[2]]
    if k == "adj":
        return abs(pos[c[1]] - pos[c[2]]) == 1
    if k == "notadj":
        return abs(pos[c[1]] - pos[c[2]]) != 1
    if k == "end":
        return pos[c[1]] in (1, max(pos.values()))
    if k == "between":
        return min(pos[c[2]], pos[c[3]]) < pos[c[1]] < max(pos[c[2]], pos[c[3]])
    if k == "gap":
        return abs(pos[c[1]] - pos[c[2]]) - 1 == c[3]
    if k == "notpos":
        return pos[c[1]] != c[2]
    if k == "parity":
        return (pos[c[1]] % 2 == 1) == bool(c[2])
    raise ValueError(k)


def deduction_row(rng, diff, n=5, noun="frame", nouns_plural="frames", names=None):
    names = rng.sample(names or SURNAMES, n)
    allperms = [dict(zip(names, p)) for p in itertools.permutations(range(1, n + 1))]
    truth = rng.choice(allperms)

    def cand():
        kind = rng.choice(["left", "left", "adj", "notadj", "end", "between", "gap", "notpos", "parity"])
        x, y, z = rng.sample(names, 3)
        if kind == "left":
            return ("left", x, y)
        if kind in ("adj", "notadj"):
            return (kind, x, y)
        if kind == "end":
            return ("end", x)
        if kind == "between":
            return ("between", x, y, z)
        if kind == "gap":
            return ("gap", x, y, abs(truth[x] - truth[y]) - 1)
        if kind == "notpos":
            return ("notpos", x, rng.randint(1, n))
        return ("parity", x, truth[x] % 2 == 1)

    clues: list = []
    sols = allperms
    guard = 0
    while len(sols) > 1 and guard < 4000:
        guard += 1
        c = cand()
        if not _clue_true(c, truth) or c in clues:
            continue
        new = [s for s in sols if _clue_true(c, s)]
        if len(new) < len(sols):
            clues.append(c)
            sols = new
    assert len(sols) == 1 and sols[0] == truth, "deduction puzzle not unique"
    # prune redundant clues, keep the puzzle uniquely solvable
    for c in list(clues):
        rest = [x for x in clues if x != c]
        if sum(1 for s in allperms if all(_clue_true(x, s) for x in rest)) == 1:
            clues = rest
    assert sum(1 for s in allperms if all(_clue_true(x, s) for x in clues)) == 1
    rng.shuffle(clues)
    ask_pos = rng.randint(1, n)
    who = next(nm for nm, p in truth.items() if p == ask_pos)
    texts = [_clue_text(c, noun) for c in clues]
    order = sorted(names, key=lambda nm: truth[nm])
    return Made("deduction", "The Row of Ancestors", [step(
        f"{n} figures stand in a row of numbered {nouns_plural}, numbered 1 to {n} from the left: " + ", ".join(f"the {nm}" for nm in sorted(names)) +
        f". Use the clues, then answer: who stands in {noun} number {ask_pos}? (Just the title, like Baron.)",
        "text", who.lower(), clues=texts, placeholder="a title", answers=[f"The {who}"])], [
        "Start with the clue that rules out the most positions, such as a figure at an end.",
        "Work out which figures are certainly NOT next to each other, then try the possible arrangements.",
        f"From the left, the order begins: " + ", ".join(order[:2]) + ".",
        f"The full row from the left is: " + ", ".join(order) + f". The answer is the {who}."],
        meta={"who": who})


def knights(rng, diff, n=4):
    names = rng.sample(["Aldric", "Brynn", "Corvin", "Drusilla", "Edmund", "Fenella", "Gideon", "Hester"], n)
    # keep initials distinct
    names = list({nm[0]: nm for nm in names}.values())
    while len(names) < n:
        names = rng.sample(["Aldric", "Brynn", "Corvin", "Drusilla", "Edmund", "Fenella", "Gideon", "Hester"], n)
        names = list({nm[0]: nm for nm in names}.values())
    names.sort()

    def stmt_text(i, s):
        k = s[0]
        P = lambda j: names[j]
        if k == "is":
            return f"{P(i)} says: \"{P(s[1])} is a {'knight' if s[2] else 'knave'}.\""
        if k == "xor":
            return f"{P(i)} says: \"Exactly one of {P(s[1])} and {P(s[2])} is a knight.\""
        if k == "same":
            return f"{P(i)} says: \"{P(s[1])} and {P(s[2])} are the same kind.\""
        if k == "both":
            return f"{P(i)} says: \"{P(s[1])} and {P(s[2])} are both knaves.\""
        if k == "count":
            return f"{P(i)} says: \"Exactly {s[1]} of the {n} of us {'is a knight' if s[1] == 1 else 'are knights'}.\""
        raise ValueError(k)

    def stmt_value(s, a):  # a: tuple of bools, True = knight
        k = s[0]
        if k == "is":
            return a[s[1]] == bool(s[2])
        if k == "xor":
            return a[s[1]] != a[s[2]]
        if k == "same":
            return a[s[1]] == a[s[2]]
        if k == "both":
            return (not a[s[1]]) and (not a[s[2]])
        return sum(a) == s[1]

    assigns = list(itertools.product([True, False], repeat=n))
    for _ in range(20000):
        stmts = []
        for i in range(n):
            kind = rng.choice(["is", "xor", "same", "both", "count"])
            others = [j for j in range(n) if j != i]
            if kind == "is":
                stmts.append(("is", rng.choice(others), rng.random() < 0.5))
            elif kind == "count":
                stmts.append(("count", rng.randint(1, n - 1)))
            else:
                j, k = rng.sample(range(n), 2)
                stmts.append((kind, j, k))
        good = [a for a in assigns if all(stmt_value(stmts[i], a) == a[i] for i in range(n))]
        if len(good) == 1 and 1 <= sum(good[0]) <= n - 1:
            sol = good[0]
            break
    else:
        raise RuntimeError("no unique knights puzzle found")
    knights_initials = "".join(sorted(names[i][0] for i in range(n) if sol[i]))
    return Made("logic", "The Island of Liars", [step(
        f"In the haunted village, every resident is either a KNIGHT (always tells the truth) or a KNAVE (always lies). {n} residents speak. Work out who is who. "
        f"Answer with the first letters of the names of all the KNIGHTS in alphabetical order, with no spaces (for example AC).",
        "code", knights_initials, clues=[stmt_text(i, stmts[i]) for i in range(n)], placeholder="initials of the knights")], [
        "Try assuming the first speaker is a knight and follow what that forces. If you hit a contradiction, they are a knave.",
        "A statement from a knave must be FALSE. Negate it to learn something certain.",
        f"There are exactly {sum(sol)} knights.",
        f"The knights are {', '.join(names[i] for i in range(n) if sol[i])}. The code is {knights_initials}."],
        meta={"code": knights_initials})


def logic_grid(rng, diff):
    cats = {
        "colour": ["red", "green", "black", "white"],
        "pet": ["cat", "raven", "bat", "toad"],
        "drink": ["tea", "wine", "milk", "blood"],
    }
    names = list(cats)
    truth = {c: rng.sample(v, len(v)) for c, v in cats.items()}  # truth[c][house-1] = item
    house_of = {(c, truth[c][h]): h + 1 for c in cats for h in range(4)}
    items = [(c, v) for c in cats for v in cats[c]]

    def cand():
        k = rng.choice(["same", "same", "left", "next", "pos", "notsame"])
        a, b = rng.sample(items, 2)
        if a[0] == b[0] and k in ("same",):
            return None
        if k == "pos":
            return ("pos", a, house_of[a])
        return (k, a, b)

    def true(c):
        if c[0] == "pos":
            return house_of[c[1]] == c[2]
        ha, hb = house_of[c[1]], house_of[c[2]]
        return {"same": ha == hb, "left": ha < hb, "next": abs(ha - hb) == 1, "notsame": ha != hb}[c[0]]

    def check(c, hm):  # hm: dict (cat,val)->house
        if c[0] == "pos":
            return hm[c[1]] == c[2]
        ha, hb = hm[c[1]], hm[c[2]]
        return {"same": ha == hb, "left": ha < hb, "next": abs(ha - hb) == 1, "notsame": ha != hb}[c[0]]

    perms = list(itertools.permutations(range(1, 5)))
    worlds = []
    for p0 in perms:
        for p1 in perms:
            for p2 in perms:
                hm = {}
                for ci, p in enumerate((p0, p1, p2)):
                    for vi, v in enumerate(cats[names[ci]]):
                        hm[(names[ci], v)] = p[vi]
                worlds.append(hm)
    sols = worlds
    clues = []
    guard = 0
    while len(sols) > 1 and guard < 5000:
        guard += 1
        c = cand()
        if c is None or not true(c) or c in clues:
            continue
        new = [w for w in sols if check(c, w)]
        if len(new) < len(sols):
            clues.append(c)
            sols = new
    assert len(sols) == 1
    for c in list(clues):
        rest = [x for x in clues if x != c]
        if sum(1 for w in worlds if all(check(x, w) for x in rest)) == 1:
            clues = rest

    def txt(c):
        d = lambda it: f"the {it[1]}" if it[0] != "colour" else f"the {it[1]} house"
        word = {"same": "belongs to the same house as", "left": "is somewhere to the left of", "next": "is directly next to", "notsame": "is NOT in the same house as"}
        if c[0] == "pos":
            return f"{d(c[1]).capitalize()} is in house {c[2]}."
        return f"{d(c[1]).capitalize()} {word[c[0]]} {d(c[2])}."

    colour = rng.choice(cats["colour"])
    h = house_of[("colour", colour)]
    pet = truth["pet"][h - 1]
    rng.shuffle(clues)
    return Made("deduction", "The Four Houses", [step(
        "Four houses stand in a row, numbered 1 to 4 from the left. Each has its own colour, its own pet and its own drink (no two houses share any of these). "
        f"Colours: red, green, black, white. Pets: cat, raven, bat, toad. Drinks: tea, wine, milk, blood. Which pet lives in the {colour} house?",
        "text", pet, clues=[txt(c) for c in clues], placeholder="a pet")], [
        "Draw four columns (houses) and three rows (colour, pet, drink). Start with clues that fix a house number.",
        "Clues saying two things are in the SAME house let you merge them into one column entry.",
        f"The {colour} house is house {h}.",
        f"The pet in the {colour} house is the {pet}."],
        meta={"pet": pet})


def latin_square(rng, diff):
    n = 4
    base = [[(r + c) % n + 1 for c in range(n)] for r in range(n)]
    rows = rng.sample(range(n), n)
    cols = rng.sample(range(n), n)
    sym = rng.sample(range(1, n + 1), n)
    sq = [[sym[base[rows[r]][cols[c]] - 1] for c in range(n)] for r in range(n)]

    def count(grid, limit=2):
        for r in range(n):
            for c in range(n):
                if grid[r][c] == 0:
                    total = 0
                    for v in range(1, n + 1):
                        if all(grid[r][k] != v for k in range(n)) and all(grid[k][c] != v for k in range(n)):
                            grid[r][c] = v
                            total += count(grid, limit)
                            grid[r][c] = 0
                            if total >= limit:
                                return total
                    return total
        return 1

    cells = [(r, c) for r in range(n) for c in range(n)]
    rng.shuffle(cells)
    grid = [row[:] for row in sq]
    removed = 0
    for r, c in cells:
        keep = grid[r][c]
        grid[r][c] = 0
        if count([row[:] for row in grid]) != 1:
            grid[r][c] = keep
        else:
            removed += 1
    target_row = rng.choice([r for r in range(n) if 0 in grid[r]])
    shown = [["?" if grid[r][c] == 0 else str(grid[r][c]) for c in range(n)] for r in range(n)]
    code = "".join(str(sq[target_row][c]) for c in range(n))
    return Made("patterns", "The Cursed Tile Square", [step(
        f"A 4 x 4 floor of tiles is numbered so that every row and every column contains each of 1, 2, 3 and 4 exactly once. Some tiles have crumbled to '?'. "
        f"Read the numbers of ROW {target_row + 1} from left to right as a 4-digit code.",
        "code", code, table={"columns": ["A", "B", "C", "D"], "rows": shown}, placeholder="4 digits")], [
        "Fill in any tile where only one number can fit its row and column.",
        "Work tile by tile: a missing number in a row cannot appear anywhere in the same column.",
        f"Row {target_row + 1} starts with {code[0]}{code[1]}.",
        f"The code is {code}."])


def crt_puzzle(rng, diff, three=False):
    mods = rng.choice([(3, 5, 7), (4, 5, 7), (3, 4, 5), (5, 7, 9)]) if three else rng.choice([(5, 7), (4, 9), (6, 7), (8, 9)])
    lcm = math.lcm(*mods)
    target = rng.randint(lcm // 4, lcm - 1)
    rems = [target % m for m in mods]
    cap = lcm + 1
    sols = [x for x in range(1, cap) if all(x % m == r for m, r in zip(mods, rems))]
    assert sols == [target]
    parts = ", ".join(f"{r} left over when grouped in {m}s" for m, r in zip(mods, rems))
    return Made("numerical_reasoning", "The Counted Candles", [step(
        f"A coven counts its candles. The total is more than 0 and at most {lcm}. When they group the candles, there are {parts}. How many candles are there?",
        "numeric", target, placeholder="number of candles")], [
        f"Start with the biggest group size ({max(mods)}) and list the numbers that leave the right remainder.",
        "Check each of those numbers against the other conditions.",
        f"The answer is between {max(1, target - 10)} and {target + 10}.",
        f"The answer is {target}."])


# =====================================================================================
# combiners: they need the clue numbers revealed by two earlier puzzles
# =====================================================================================
def comb_arith(rng, diff, a, b, A, B):
    ans = a * b + a + b
    return Made("mathematics", "The Two-Clue Lock", [step(
        f"The lock needs both clues you found. Let P be the number from the {A} and Q the number from the {B} (check your clue notes). Calculate P x Q + P + Q.",
        "numeric", ans, placeholder="the result")], [
        f"Open your clue notes: you need the number from the {A} and the number from the {B}.",
        "Multiply the two numbers, then add each of them once.",
        f"P x Q = {a * b}.",
        f"{a * b} + {a} + {b} = {ans}."])


def comb_mod(rng, diff, a, b, A, B):
    m = rng.choice([7, 9, 11])
    ans = (a * a + b) % m
    return Made("numerical_reasoning", "The Remainder Seal", [step(
        f"Let P be the number found at the {A} and Q the number found at the {B}. The seal turns to the remainder when P x P + Q is divided by {m}. What is that remainder?",
        "numeric", ans, placeholder="the remainder")], [
        "Find P and Q in your clue notes first.",
        f"P x P + Q = {a * a + b}.",
        f"Divide {a * a + b} by {m}: it goes in {(a * a + b) // m} times.",
        f"The remainder is {ans}."])


def comb_seq(rng, diff, a, b, A, B):
    ask = 9
    t = [a, b]
    while len(t) < ask:
        t.append(t[-1] + t[-2])
    return Made("sequences", "The Echoing Pair", [step(
        f"A sequence begins with the number from the {A} followed by the number from the {B}. After that, each term is the sum of the two terms before it. What is the {ordinal(ask)} term?",
        "numeric", t[ask - 1], placeholder=f"the {ordinal(ask)} term")], [
        f"Your first two terms are in your clue notes (the {A}, then the {B}).",
        "Add the first two to get the third, then keep adding the last two.",
        f"Terms so far: {', '.join(map(str, t[:6]))}.",
        f"The {ordinal(ask)} term is {t[ask - 1]}."])


def comb_paths(rng, diff, a, b, A, B):
    ans = math.comb(a + b, a)
    return Made("mathematics", "The Staircase of Choices", [step(
        f"A courtyard is a grid. Its width is the number from the {A} and its height is the number from the {B}, counted in steps. Starting at one corner you may only walk RIGHT or UP. How many different routes lead to the opposite corner?",
        "numeric", ans, placeholder="number of routes")], [
        "You need exactly (width) steps right and (height) steps up, in some order.",
        "The number of routes is the number of ways to choose which steps are 'right'.",
        f"That is C({a + b}, {a}), the number of ways to choose {a} items from {a + b}.",
        f"The answer is {ans}."])


def comb_crt(rng, diff, a, b, A, B):
    # a, b are coprime and > 2 by construction of the caller
    r1, r2 = rng.randint(1, a - 1), rng.randint(1, b - 1)
    floor_n = rng.randint(20, 60)
    ans = next(x for x in range(floor_n + 1, floor_n + a * b + 1) if x % a == r1 and x % b == r2)
    return Made("numerical_reasoning", "The Twin Remainders", [step(
        f"Let P be the number from the {A} and Q the number from the {B}. Find the smallest number above {floor_n} that leaves a remainder of {r1} when divided by P and a remainder of {r2} when divided by Q.",
        "numeric", ans, placeholder="the number")], [
        "Look up P and Q in your clue notes.",
        f"List the numbers above {floor_n} that leave remainder {r1} when divided by P.",
        f"Check each of them against the second condition (remainder {r2} when divided by Q).",
        f"The answer is {ans}."])


def comb_vigenere(rng, diff, a, key, A, B):
    word = rng.choice([w for w in WORDS if 5 <= len(w) <= 7])
    cipher = vigenere_enc(word, key)
    assert vigenere_dec(cipher, key) == word
    letters_sum = sum(ALPHA.index(c) + 1 for c in word)
    ans2 = letters_sum % a if a > 1 else 0
    return Made("cryptic", "The Keyed Cipher", [
        step(
            f"The final note is written in a keyed code. The KEYWORD is the word you found at the {B}. To decode, write the keyword repeatedly under the message; "
            f"for each letter, move the message letter BACK by the position of the key letter (A = 0, B = 1, C = 2 ... Z = 25). The message is {cipher}. What does it say?",
            "text", word.lower(), placeholder="the decoded word", ok="The message makes sense. One more twist remains."),
        step(
            f"Now add up the alphabet positions of the letters of the decoded word (A = 1, B = 2 ... Z = 26) and divide the total by P, the number from the {A}. What is the REMAINDER?",
            "numeric", ans2, placeholder="the remainder")], [
        f"The keyword is in your clue notes (from the {B}). Write it repeatedly under {cipher}.",
        f"Subtract the key letter's position from each message letter. The first key letter is {key[0]} (= {ALPHA.index(key[0])}).",
        f"The decoded word has {len(word)} letters and starts with {word[0]}. For part 2 the letter total is {letters_sum}.",
        f"The word is {word}. Part 2: {letters_sum} divided by P leaves {ans2}."])


# registry --------------------------------------------------------------------------
INDEPENDENT = {
    "medium": [seq_arith_diff, seq_geo_diff, cipher_caesar, word_rate, table_stat],
    "medium_plus": [seq_interleave, cipher_crib, lambda r, d: deduction_row(r, d, 5, "pew", "pews"), lambda r, d: crt_puzzle(r, d, False),
                    probability, table_stat, word_rate],
    "hard": [lambda r, d: deduction_row(r, d, 6, "chair", "chairs"), lambda r, d: knights(r, d, 4), probability, counting, grid_paths,
             base_conv, latin_square, seq_polynomial, seq_fib_like, lambda r, d: crt_puzzle(r, d, True)],
    "very_hard": [lambda r, d: deduction_row(r, d, 7, "stall", "stalls"), lambda r, d: knights(r, d, 5), logic_grid, seq_recurrence,
                  latin_square, counting, probability, grid_paths, lambda r, d: crt_puzzle(r, d, True)],
}
COMBINERS = {
    "medium": [comb_arith],
    "medium_plus": [comb_mod, comb_seq],
    "hard": [comb_seq, comb_paths, comb_crt, comb_mod],
    "very_hard": [comb_vigenere, comb_crt, comb_paths],
}
