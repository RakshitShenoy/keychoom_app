/* Game view (Phases 4-9). The browser only RENDERS the state the server sends and posts INTENTS
 * (an answer, a code, a hint level, "climb"). It never decides an outcome, a clock or a fragment.
 * Every piece of text is inserted with textContent via HH.el (no innerHTML). The only values kept in
 * the browser are presentation preferences (effects level, sound) - never game state. */
(function () {
  const HH = window.HH;
  const el = HH.el;
  const $ = function (id) { return document.getElementById(id); };

  let gameCode = null;
  let state = null;
  let built = false;
  let clockOffsetMs = 0;       // server_now - local now, measured on every state: display only
  let tickTimer = null;
  let beatTimer = null;
  let beatObject = null;
  let message = null;          // {kind: "good"|"bad"|"info", text}
  let draft = { answer: "", vault: "", door: "" };
  let connection = "connecting";
  let cooldownUntil = 0;       // visual only: the server enforces the real cooldown
  let busy = false;
  const sigs = {};             // section signature cache: a section is only rebuilt when its data changed
  const seenScares = {};
  let scareActive = false;

  const ANSWER_COOLDOWN_MS = 2000;
  const COOLDOWN_CODES = { ANSWER_COOLDOWN: true, VAULT_COOLDOWN: true };

  // ------------------------------------------------------------------ presentation preferences
  const FX_KEY = "hh_fx", SOUND_KEY = "hh_sound";
  function storeGet(key) { try { return window.localStorage.getItem(key); } catch (e) { return null; } }
  function storeSet(key, value) { try { window.localStorage.setItem(key, value); } catch (e) { /* private mode */ } }
  function defaultFx() {
    return (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) ? "reduced" : "full";
  }
  function fx() { const v = storeGet(FX_KEY); return v === "full" || v === "reduced" || v === "off" ? v : defaultFx(); }
  function applyFx() { document.documentElement.setAttribute("data-fx", fx()); }
  function soundOn() { return storeGet(SOUND_KEY) === "on"; }
  applyFx();

  // ------------------------------------------------------------------ audio hooks (synthesised, no files)
  const audio = (function () {
    let ctx = null;
    function context() {
      if (!ctx) { const C = window.AudioContext || window.webkitAudioContext; if (C) { try { ctx = new C(); } catch (e) { ctx = null; } } }
      if (ctx && ctx.state === "suspended") { ctx.resume(); }
      return ctx;
    }
    function tone(freq, dur, type, vol, slideTo) {
      const c = context(); if (!c) { return; }
      const o = c.createOscillator(), g = c.createGain();
      o.type = type || "sine"; o.frequency.value = freq;
      if (slideTo) { o.frequency.exponentialRampToValueAtTime(slideTo, c.currentTime + dur); }
      g.gain.setValueAtTime(vol, c.currentTime); g.gain.exponentialRampToValueAtTime(0.0001, c.currentTime + dur);
      o.connect(g); g.connect(c.destination); o.start(); o.stop(c.currentTime + dur);
    }
    function noise(dur, vol) {
      const c = context(); if (!c) { return; }
      const buf = c.createBuffer(1, Math.floor(c.sampleRate * dur), c.sampleRate), d = buf.getChannelData(0);
      for (let i = 0; i < d.length; i++) { d[i] = (Math.random() * 2 - 1) * (1 - i / d.length); }
      const s = c.createBufferSource(), g = c.createGain(); s.buffer = buf; g.gain.value = vol;
      s.connect(g); g.connect(c.destination); s.start();
    }
    return {
      // hooks: HH.audio.play("click" | "good" | "bad" | "stinger" | "creak"). Silent unless the player turned sound on.
      play: function (name) {
        if (!soundOn() || fx() === "off") { return; }
        if (name === "click") { tone(220, 0.08, "triangle", 0.05); }
        else if (name === "creak") { tone(90, 0.5, "sawtooth", 0.04, 60); }
        else if (name === "good") { tone(440, 0.18, "sine", 0.06); setTimeout(function () { tone(660, 0.25, "sine", 0.06); }, 120); }
        else if (name === "bad") { tone(150, 0.3, "square", 0.05, 90); }
        else if (name === "stinger") { if (fx() === "full") { noise(0.7, 0.28); tone(55, 0.9, "sawtooth", 0.2, 30); } else { tone(110, 0.8, "sine", 0.05); } }
      },
      unlock: context
    };
  })();
  HH.audio = audio;

  // ------------------------------------------------------------------ helpers
  function url(suffix) { return "/api/games/" + encodeURIComponent(gameCode) + "/play" + suffix; }
  function say(kind, text) { message = text ? { kind: kind, text: text } : null; }
  function fmt(seconds) {
    const s = Math.max(0, Math.floor(seconds));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    const two = function (n) { return (n < 10 ? "0" : "") + n; };
    return (h ? h + ":" + two(m) : m) + ":" + two(r);
  }
  function nowServerMs() { return Date.now() + clockOffsetMs; }
  function sig(x) { return JSON.stringify(x); }
  function section(name, signature, build) {
    const host = $("hh-" + name);
    if (!host) { return; }
    if (sigs[name] === signature) { return; }
    // keep what the player is typing: focus + caret survive a rebuild of this section
    const active = document.activeElement;
    const keep = active && host.contains(active) && active.id ? { id: active.id, a: active.selectionStart, b: active.selectionEnd } : null;
    sigs[name] = signature;
    host.textContent = "";
    const kids = build();
    (Array.isArray(kids) ? kids : [kids]).forEach(function (k) { if (k) { host.appendChild(k); } });
    if (keep) {
      const again = $(keep.id);
      if (again) { again.focus(); try { again.setSelectionRange(keep.a, keep.b); } catch (e) { /* not a text input */ } }
    }
  }

  // ------------------------------------------------------------------ talking to the server
  async function act(suffix, body) {
    if (busy) { return null; }
    busy = true;
    let res;
    try { res = await HH.api(url(suffix), "POST", body); } finally { busy = false; }
    if (!res.ok) {
      const err = res.data && res.data.error;
      say("bad", HH.errorText(res));
      const code = err && err.code;
      if (code && COOLDOWN_CODES[code]) { cooldownUntil = Date.now() + ANSWER_COOLDOWN_MS; }
      if (code === "TEAM_ELIMINATED" || code === "NOT_INVESTIGATING" || code === "OBJECT_BUSY" || code === "OBJECT_SOLVED" ||
          code === "FLOOR_COMPLETED" || code === "GAME_OVER" || code === "FLOOR_NOT_COMPLETE") { await refresh(true); } else { sigs.msg = null; render(); }
      return res;
    }
    const result = res.data.result;
    if (result) { handleResult(result); } else { say(null); }
    if (res.data.state) { setState(res.data.state); }
    return res;
  }

  function handleResult(result) {
    const wrong = result.result === "WRONG" || result.result === "INVALID";
    say(wrong ? "bad" : "good", result.message || result.reveal_text || null);
    if (result.result === "WRONG") { cooldownUntil = Date.now() + ANSWER_COOLDOWN_MS; audio.play("bad"); }
    if (result.result === "HINT") { say("good", "Hint " + result.level + ": " + result.text); }
    if (result.result === "FINISHED" || result.result === "TIME_UP") {
      say(result.passed ? "good" : "bad", "Quiz finished: " + result.score + "/" + (result.total || "?") +
        (result.passed ? " - passed!" : " - not enough correct answers."));
    }
    if (result.result === "CONFIRMED" && result.reveal_text) { say("good", result.reveal_text); }
    if (result.result === "CORRECT") { audio.play("good"); if (result.reveal_text) { say("good", "Correct! " + result.reveal_text); } }
    if (result.result === "RECORDED") { say(null); }
    if (result.result === "TRAP") {
      say("info", result.message);
      if (result.jumpscare) { showScare(result.jumpscare); }
    }
    sigs.msg = null;
  }

  async function refresh(keepMessage) {
    if (!gameCode) { return; }
    const res = await HH.api(url("/state"));
    if (res.ok && res.data.state) {
      if (!keepMessage) { say(null); }
      sigs.msg = null;
      setState(res.data.state);
    }
  }

  function setState(next) {
    state = next;
    if (state.timer && state.timer.server_now) { clockOffsetMs = Date.parse(state.timer.server_now) - Date.now(); }
    render();
  }

  // ------------------------------------------------------------------ the shell (built once)
  function buildShell() {
    const root = $("game");
    root.classList.remove("hidden");
    root.textContent = "";
    root.appendChild(el("div", { class: "hh-reconn hidden", id: "hh-reconn", role: "status",
      text: "Reconnecting to the house... your progress is safe on the server." }));
    root.appendChild(el("div", { class: "hh-game", id: "hh-game" }, [
      el("div", { class: "hh-hud", id: "hh-hud" }),
      el("div", { id: "hh-msg", role: "status", "aria-live": "polite" }),
      el("div", { class: "hh-main" }, [
        el("div", { class: "hh-col" }, [
          el("div", { class: "hh-stage", id: "hh-stage" }, [
            el("div", { class: "hh-light" }), el("div", { class: "hh-fog" }), el("div", { class: "hh-fog two" }),
            el("div", { class: "hh-title", id: "hh-roomtitle" }),
            el("div", { class: "hh-objects", id: "hh-objects" }), el("div", { class: "hh-tray", id: "hh-tray" }),
            el("div", { class: "hh-vignette" }), el("div", { class: "hh-dark", id: "hh-dark" })
          ]),
          el("div", { class: "hh-intro", id: "hh-roomintro" })
        ]),
        el("div", { class: "hh-col" }, [
          el("div", { id: "hh-task" }), el("div", { id: "hh-door" }), el("div", { id: "hh-vault" }), el("div", { id: "hh-clues" }),
          el("div", { id: "hh-hints" }), el("div", { id: "hh-team" })
        ])
      ]),
      el("div", { id: "hh-cover" })
    ]));
    if (!$("hh-scare")) {
      document.body.appendChild(el("div", { id: "hh-scare", role: "alertdialog", "aria-label": "Jumpscare", "aria-live": "assertive" }));
    }
    built = true;
    startAtmosphere();
  }

  // ------------------------------------------------------------------ render
  function render() {
    if (!state) { return; }
    if (!built) { buildShell(); }
    $("hh-game").className = "hh-game" + (connection === "live" ? "" : " is-offline");
    $("hh-reconn").classList.toggle("hidden", connection === "live" || connection === "connecting");
    const room = state.room;
    $("hh-stage").className = "hh-stage floor-" + (room ? room.floor : 1);

    section("hud", sig([room && room.floor, state.team, state.fragments, state.hints.remaining, state.hints.max, state.progress,
      state.timer.deadline_at, state.timer.expired, state.timer.paused, connection, fx(), soundOn(), state.game.status]), hudBlock);
    section("msg", sig(message), function () { return message ? el("div", { class: "hh-msg " + message.kind, text: message.text }) : null; });
    if (!room) { return; }
    $("hh-roomtitle").textContent = room.title;
    $("hh-roomintro").textContent = room.intro;
    section("objects", sig([room.objects, room.vault.status, room.floor_status, state.members, state.game.status, state.timer.expired]), objectsBlock);
    section("tray", sig(state.members), trayBlock);
    section("task", sig([state.my_investigation, draft.answer === "", message && message.kind, cooldownUntil > Date.now(), room.floor_status,
      state.can_advance, state.game.status, state.timer.expired, room.vault.status]), taskBlock);
    section("vault", sig([room.vault, room.floor_status, draft.vault === "", cooldownUntil > Date.now(), message && message.kind, state.game.status, state.timer.expired]), vaultBlock);
    section("door", sig([state.final_door, state.game.status, room.floor, room.floor_status, cooldownUntil > Date.now(), message && message.kind]), doorBlock);
    section("clues", sig(room.clues), cluesBlock);
    section("hints", sig([state.hints, state.timer.expired, room.floor_status]), hintsBlock);
    section("team", sig([state.team, state.members]), teamBlock);
    section("cover", sig([state.game, state.timer.expired, state.team.status]), coverBlock);
    syncHeartbeat();
    tick();
  }

  function hudBlock() {
    const room = state.room;
    const prog = state.progress || { required_total: 1, required_solved: 0 };
    const pct = room && room.floor_status === "COMPLETED" ? 100 : Math.round(100 * prog.required_solved / Math.max(1, prog.required_total));
    const frags = state.fragments.map(function (f) {
      return el("span", { class: "hh-frag" + (f.collected ? " got" : "") + (room && f.floor === room.floor ? " now" : ""), role: "listitem",
        title: f.collected ? f.label : "Floor " + f.floor + " fragment (not found yet)",
        "aria-label": "Floor " + f.floor + (f.collected ? " fragment collected" : " fragment missing"), "data-collected": f.collected ? "1" : "0",
        text: f.collected ? (f.rune || "*") : String(f.floor) });
    });
    const low = state.timer.deadline_at && !state.timer.expired && (Date.parse(state.timer.deadline_at) - nowServerMs()) < 300000;
    return [
      el("div", { class: "hh-floor" }, [el("b", { id: "hh-floorlabel", text: "Floor " + (room ? room.floor : "?") + " of " + state.game.total_floors }), el("span", { text: state.team.name })]),
      el("div", { class: "hh-frags", role: "list", "aria-label": "Key fragments" }, frags),
      el("div", { class: "hh-stats" }, [
        el("span", { class: "hh-clock" + (low ? " low pulse" : ""), id: "hh-clock", title: "Time left for your team (the server keeps the real clock)", text: "--:--" }),
        el("span", { class: "hh-pill hint", id: "hh-hintpill", title: "Hints your team can still use (they cost time)", text: "Hints " + state.hints.remaining + "/" + state.hints.max }),
        el("span", { class: "hh-pill hh-conn " + connection, id: "hh-conn", title: "Connection to the house. Click to retry.", text: connectionLabel(),
          onclick: function () { const b = $("conn-status"); if (b) { b.click(); } } }),
        el("button", { type: "button", class: "small hh-fx", id: "hh-fxbtn", title: "Visual effects level (accessibility): full, reduced or off", text: "FX: " + fx(),
          onclick: function () { const order = ["full", "reduced", "off"]; storeSet(FX_KEY, order[(order.indexOf(fx()) + 1) % 3]); applyFx(); sigs.hud = null; render(); } }),
        el("button", { type: "button", class: "small hh-fx", title: "Sound effects on/off", text: soundOn() ? "Sound: on" : "Sound: off",
          onclick: function () { storeSet(SOUND_KEY, soundOn() ? "off" : "on"); audio.unlock(); audio.play("click"); sigs.hud = null; render(); } })
      ]),
      el("div", { class: "hh-bar", title: "Progress on this floor" }, [el("i", { style: "width:" + pct + "%" })])
    ];
  }

  function connectionLabel() {
    return { live: "LIVE", connecting: "CONNECTING...", reconnecting: "RECONNECTING...", offline: "OFFLINE", failed: "DISCONNECTED - retry" }[connection] || connection;
  }

  function objectStateOf(o) {
    if (state.game.status !== "RUNNING" || state.timer.expired) { return { cls: "locked", tag: state.timer.expired ? "expired" : "over" }; }
    if (o.status === "SOLVED") { return { cls: "solved", tag: "solved" }; }
    if (o.held_by_me) { return { cls: "mine", tag: "investigating" }; }
    if (o.status === "BEING_INVESTIGATED") { return { cls: "occupied", tag: (o.holder || "someone") + " is here" }; }
    if (o.status === "LOCKED") { return { cls: "locked", tag: "locked" }; }
    return { cls: "available", tag: o.optional ? "optional" : "available" };
  }

  function pawnsAt(objectId) {
    return state.members.filter(function (m) { return m.investigating_id === objectId; }).map(function (m, i) {
      return el("span", { class: "hh-pawn" + (i ? " p" + Math.min(i + 1, 3) : ""), style: "background:" + m.pawn_color, title: m.name + (m.is_me ? " (you)" : ""),
        text: (m.name || "?").slice(0, 1).toUpperCase() });
    });
  }

  function objectsBlock() {
    const room = state.room;
    const out = room.objects.map(function (o) {
      const st = objectStateOf(o);
      const label = o.label + " - " + st.tag;
      return el("button", {
        type: "button", class: "hh-obj " + st.cls + (o.optional ? " optional" : ""), style: "left:" + o.x + "%;top:" + o.y + "%",
        "aria-label": label, title: label, "data-object": o.id, "data-state": st.cls,
        onclick: function () { investigate(o, st); }
      }, [
        el("span", { class: "sigil" }, [o.icon || "?"].concat(pawnsAt(o.id))),
        el("span", { class: "lab", text: o.label }), el("span", { class: "tag", text: st.tag })
      ]);
    });
    const v = room.vault;
    const vst = room.floor_status === "COMPLETED" ? { cls: "solved", tag: "open" } : v.status === "LOCKED" ? { cls: "locked", tag: "locked" } : { cls: "available", tag: "enter code" };
    out.push(el("button", {
      type: "button", class: "hh-obj vault " + vst.cls, style: "left:" + v.x + "%;top:" + v.y + "%", "aria-label": v.label + " - " + vst.tag,
      "data-object": "vault", "data-state": vst.cls,
      onclick: function () { const b = $("hh-vault"); if (b && b.scrollIntoView) { b.scrollIntoView({ behavior: "smooth", block: "center" }); } const i = $("gm-vault"); if (i) { i.focus(); } }
    }, [el("span", { class: "sigil" }, ["\uD83D\uDDDD"]), el("span", { class: "lab", text: v.label }), el("span", { class: "tag", text: vst.tag })]));
    return out;
  }

  function trayBlock() {
    return state.members.map(function (m) {
      return el("span", { class: "hh-mate" + (m.connected ? "" : " off") + (m.is_me ? " me" : ""), title: m.connected ? "online" : "offline (their place is saved)" }, [
        el("i", { style: "background:" + m.pawn_color }), m.name + (m.is_leader ? " (leader)" : ""),
        m.investigating ? el("small", { text: "@ " + m.investigating }) : null
      ]);
    });
  }

  // ------------------------------------------------------------------ the active task
  function taskBlock() {
    const room = state.room;
    const inv = state.my_investigation;
    if (state.game.status === "FINISHED") { return el("div", { class: "card hh-panel hh-idle", text: "The game is over." }); }
    if (state.timer.expired) { return el("div", { class: "card hh-panel hh-idle", text: "Your time has run out. The house keeps you." }); }
    if (room.floor_status === "COMPLETED") { return completedCard(); }
    if (inv) { return challengeCard(inv); }
    return el("div", { class: "card hh-panel hh-idle" }, [
      el("div", { text: "Choose an object in the room to investigate it." }),
      el("p", { class: "hh-meta", text: "Teammates can work on different objects at the same time. Click or tap an object." })
    ]);
  }

  function completedCard() {
    const fr = state.room.fragment;
    const kids = [el("h2", { text: "Floor " + state.room.floor + " complete" }),
      el("p", { class: "hh-prompt", text: "The vault opens. You take the " + fr.label + "." })];
    if (state.can_advance) {
      kids.push(el("button", { type: "button", class: "hh-big", id: "hh-climb", text: "Climb to Floor " + (state.room.floor + 1),
        onclick: function () { act("/floor/next"); } }));
    } else if (state.room.floor >= state.game.total_floors) {
      kids.push(el("p", { class: "hh-prompt", text: "Every fragment is yours. Only the final door remains." }));
    }
    return el("div", { class: "card hh-panel" }, kids);
  }

  function coolingDown() { return Date.now() < cooldownUntil; }
  function badNow() { return !!(message && message.kind === "bad" && coolingDown()); }

  function challengeCard(inv) {
    const kids = [el("h2", {}, [inv.label, el("span", { class: "hh-state", text: "investigating" })])];
    if (inv.challenge) {
      const c = inv.challenge;
      kids.push(el("h3", { text: c.title + (c.step_count > 1 ? " (part " + c.step + "/" + c.step_count + ")" : "") }));
      kids.push(el("div", { class: "hh-meta cap", text: (c.category || "").replace(/_/g, " ") + " - " + (c.difficulty || "").replace(/_/g, " ") }));
      kids.push(el("p", { class: "hh-prompt", text: c.prompt }));
      if (c.clues) { kids.push(el("ol", { class: "hh-clues" }, c.clues.map(function (t) { return el("li", { text: t }); }))); }
      if (c.table) {
        const rows = [el("tr", {}, c.table.columns.map(function (h) { return el("th", { text: h }); }))];
        c.table.rows.forEach(function (r) { rows.push(el("tr", {}, r.map(function (v) { return el("td", { text: v }); }))); });
        kids.push(el("table", {}, rows));
      }
      if (c.answer_type === "choice") {
        (c.choices || []).forEach(function (ch) {
          kids.push(el("button", { type: "button", class: "small hh-choice", text: ch.id.toUpperCase() + ". " + ch.text,
            onclick: function () { submitAnswer(inv.object_id, ch.id); } }));
        });
      } else {
        kids.push(el("div", { class: "hh-answer" + (badNow() ? " bad" : "") }, [
          el("input", { id: "gm-answer", value: draft.answer, placeholder: c.placeholder || "your answer", maxlength: "64", autocomplete: "off",
            autocapitalize: "off", "aria-label": "Your answer", oninput: function (e) { draft.answer = e.target.value; },
            onkeydown: function (e) { if (e.key === "Enter") { submitAnswer(inv.object_id, draft.answer); } } }),
          el("button", { type: "button", id: "gm-submit", text: coolingDown() ? "Wait..." : "Submit", onclick: function () { submitAnswer(inv.object_id, draft.answer); } })
        ]));
      }
    } else if (inv.quiz) {
      kids.push.apply(kids, quizBlock(inv));
    } else if (inv.dare) {
      kids.push(el("h3", { text: "A friendly dare" }));
      kids.push(el("p", { class: "hh-prompt", text: inv.dare.text }));
      kids.push(el("p", { class: "hh-meta", text: "Dares are optional and always safe. Skip any you do not want to do." }));
      kids.push(el("button", { type: "button", text: "I did it", onclick: function () { act("/objects/" + inv.object_id + "/dare/confirm"); } }));
    }
    kids.push(el("button", { type: "button", class: "small danger", text: "Leave this object",
      onclick: function () { draft.answer = ""; act("/objects/" + inv.object_id + "/release"); } }));
    return el("div", { class: "card hh-panel" }, kids);
  }

  function quizBlock(inv) {
    const q = inv.quiz;
    const out = [el("h3", { text: "Quick quiz" })];
    if (q.state === "READY" || q.state === "TIME_UP") {
      out.push(el("p", { class: "hh-prompt", text: q.questions + " questions, " + q.time_limit_seconds + " seconds, " + q.pass_score + " correct to pass. The server keeps the clock." }));
      out.push(el("button", { type: "button", class: "hh-big", text: q.attempts ? "Try again" : "Start quiz", onclick: function () { act("/objects/" + inv.object_id + "/quiz/start"); } }));
    } else {
      const left = (Date.parse(q.ends_at) - nowServerMs()) / 1000;
      out.push(el("p", { class: "hh-meta" }, ["Question " + q.question.number + "/" + q.question.total + " - time left ",
        el("strong", { id: "gm-quizclock", "data-ends": q.ends_at, text: fmt(left) })]));
      out.push(el("p", { class: "hh-prompt", text: q.question.prompt }));
      q.question.choices.forEach(function (ch) {
        out.push(el("button", { type: "button", class: "small hh-choice", text: ch.id.toUpperCase() + ". " + ch.text,
          onclick: function () { act("/objects/" + inv.object_id + "/quiz/answer", { question_id: q.question.question_id, choice: ch.id }); } }));
      });
    }
    return out;
  }

  // ------------------------------------------------------------------ vault, final door, clues, hints, team
  function vaultBlock() {
    const room = state.room, v = room.vault;
    const slots = v.revealed.map(function (c) { return el("span", { class: "hh-slot" + (c ? " on" : ""), text: c || "?" }); });
    const kids = [el("h2", {}, [v.label, el("span", { class: "hh-state", text: room.floor_status === "COMPLETED" ? "open" : v.status.toLowerCase() })]),
      el("div", { class: "hh-vault-slots", "aria-label": "Vault code pieces found so far" }, slots)];
    if (room.floor_status === "COMPLETED") {
      kids.push(el("p", { class: "hh-prompt", text: "Opened. " + room.fragment.label + " collected." }));
    } else if (v.status === "LOCKED") {
      kids.push(el("p", { class: "hh-meta", text: "Solve the objects in this room to reveal the code pieces. The vault stays locked until then." }));
    } else if (state.game.status === "RUNNING" && !state.timer.expired) {
      kids.push(el("div", { class: "hh-answer" + (badNow() ? " bad" : "") }, [
        el("input", { id: "gm-vault", value: draft.vault, placeholder: "enter the " + v.length + "-character code", maxlength: "12", autocomplete: "off",
          autocapitalize: "characters", "aria-label": "Vault code", oninput: function (e) { draft.vault = e.target.value; },
          onkeydown: function (e) { if (e.key === "Enter") { submitVault(); } } }),
        el("button", { type: "button", id: "gm-vault-btn", text: "Open vault", onclick: submitVault })
      ]));
    }
    return el("div", { class: "card hh-panel" }, kids);
  }

  function doorBlock() {
    const fd = state.final_door, room = state.room, total = state.game.total_floors;
    if (state.game.status === "FINISHED" && fd.status !== "OPENED") { return null; }
    if (fd.status === "LOCKED" && room.floor < total) {
      return el("div", { class: "hh-doorline", text: "The Final Door: " + fd.collected + " of " + fd.length + " key fragments collected" });
    }
    const runes = fd.runes.map(function (r) { return el("span", { class: "hh-rune" + (r ? " got" : ""), text: r || "?" }); });
    const kids = [el("h2", { text: "The Final Door" }), el("div", { class: "hh-runes", "aria-label": "Runes from your fragments" }, runes)];
    if (fd.status === "AVAILABLE") {
      kids.push(el("p", { class: "hh-prompt", text: "All eight fragments hum together. Enter the eight runes in floor order to open the final door." }));
      kids.push(el("div", { class: "hh-answer" + (badNow() ? " bad" : "") }, [
        el("input", { id: "gm-door", value: draft.door, placeholder: "8 runes", maxlength: "16", autocomplete: "off", autocapitalize: "characters", "aria-label": "Final door runes",
          oninput: function (e) { draft.door = e.target.value; }, onkeydown: function (e) { if (e.key === "Enter") { submitDoor(); } } }),
        el("button", { type: "button", id: "gm-door-btn", text: "Unlock the door", onclick: submitDoor })
      ]));
    } else {
      kids.push(el("p", { class: "hh-meta", text: fd.status === "OPENED" ? "The door stands open." : fd.collected + " of " + fd.length + " fragments. Finish this floor first." }));
    }
    return el("div", { class: "card hh-panel hh-door" }, kids);
  }

  function cluesBlock() {
    const clues = state.room.clues;
    if (!clues.length) { return null; }
    return el("div", { class: "card hh-panel", id: "hh-cluebox" }, [el("h2", { text: "Clue notes" })].concat(clues.map(function (c) {
      return el("div", { class: "hh-hint" }, [el("strong", { text: c.label + ": " }), c.text]);
    })));
  }

  function hintsBlock() {
    const h = state.hints;
    const kids = [el("h2", { text: "Hints" }),
      el("p", { class: "hh-meta", text: h.remaining + " of " + h.max + " left for the whole game. Each costs your team time." })];
    h.revealed.forEach(function (r) {
      kids.push(el("div", { class: "hh-hint" }, [el("strong", { text: "Hint " + r.level + (r.about ? " (" + r.about + ", floor " + r.floor + ")" : "") + ": " }), r.text]));
    });
    if (h.available) {
      const next = h.levels.find(function (lv) { return !lv.used; });
      if (next) {
        kids.push(el("button", { type: "button", class: "small", id: "hh-usehint", text: "Use hint " + next.level + " (-" + fmt(next.penalty_seconds) + ")", onclick: function () { useHint(next); } }));
      }
    }
    return el("div", { class: "card hh-panel" }, kids);
  }

  function teamBlock() {
    return el("div", { class: "card hh-panel", id: "hh-teambox" }, [el("h2", { text: state.team.name }),
      el("div", { class: "hh-meta", text: "Floors completed: " + state.team.floors_completed + " of " + state.game.total_floors })].concat(
      state.members.map(function (m) {
        return el("div", { "data-member": m.name }, [el("span", { class: "dot", style: "background:" + m.pawn_color }), m.name + (m.is_me ? " (you)" : "") + (m.is_leader ? " - leader" : "") +
          (m.connected ? "" : " - offline") + (m.investigating ? " - at " + m.investigating : "")]);
      })));
  }

  function coverBlock() {
    const g = state.game;
    if (g.status === "FINISHED") {
      const mine = g.winner && g.winner.is_my_team;
      return el("div", { class: "hh-cover", id: "hh-gameover", role: "dialog", "aria-label": "Game over" }, [el("div", { class: "box" }, [
        el("h2", { text: g.winner ? (mine ? "You escaped!" : "Too late...") : "The house wins" }),
        el("p", { id: "hh-winner", text: g.winner ? (mine ? "Your team opened the final door first. The night is yours." : g.winner.team_name + " opened the final door first. The house keeps the rest of you.")
          : "Every team ran out of time. Nobody escaped." }),
        el("button", { type: "button", text: "See the house", onclick: function () { sigs.cover = "dismissed"; $("hh-cover").textContent = ""; } })
      ])]);
    }
    if (g.paused) {  // the host paused: the server rejects every action until it resumes
      return el("div", { class: "hh-cover", id: "hh-paused", role: "alertdialog", "aria-label": "Game paused" }, [el("div", { class: "box" }, [
        el("h2", { text: "The house holds its breath" }),
        el("p", { text: "The host paused the game. Your clock is stopped. Nothing can be solved until it resumes." })
      ])]);
    }
    if (state.timer.expired || state.team.status === "ELIMINATED") {
      return el("div", { class: "hh-cover", id: "hh-timeup", role: "dialog", "aria-label": "Time is up" }, [el("div", { class: "box" }, [
        el("h2", { text: "Time is up" }), el("p", { text: "The clock struck midnight. Your team can no longer escape." }),
        el("button", { type: "button", text: "Close", onclick: function () { sigs.cover = "dismissed"; $("hh-cover").textContent = ""; } })
      ])]);
    }
    return null;
  }

  // ------------------------------------------------------------------ clocks
  function tick() {
    if (!state || !state.timer) { return; }
    const clock = $("hh-clock");
    if (clock && state.timer.deadline_at) {
      const left = state.timer.paused && state.timer.seconds_remaining !== null
        ? state.timer.seconds_remaining  // frozen by the host's pause (the server computed it)
        : (Date.parse(state.timer.deadline_at) - nowServerMs()) / 1000;
      clock.textContent = state.timer.expired || state.game.status === "FINISHED" ? "0:00" : fmt(left);
      if (!state.timer.expired && !state.timer.paused && left <= 0 && state.game.status === "RUNNING" && !tick.asked) { tick.asked = true; setTimeout(function () { tick.asked = false; refresh(true); }, 1500); }
    }
    const qc = $("gm-quizclock");
    if (qc && !state.timer.paused) { qc.textContent = fmt((Date.parse(qc.getAttribute("data-ends")) - nowServerMs()) / 1000); }
    if (cooldownUntil && Date.now() > cooldownUntil) { cooldownUntil = 0; sigs.task = null; sigs.vault = null; sigs.door = null; render(); }
  }

  function syncHeartbeat() {
    const inv = state && state.my_investigation;
    const id = inv ? inv.object_id : null;
    if (id === beatObject) { return; }
    if (beatTimer) { clearInterval(beatTimer); beatTimer = null; }
    beatObject = id;
    if (id) {
      beatTimer = setInterval(async function () {
        const res = await HH.api(url("/objects/" + id + "/heartbeat"), "POST");
        if (!res.ok) { say("info", "You are no longer investigating that."); refresh(true); }
      }, 20000);
    }
  }

  // ------------------------------------------------------------------ intents
  function investigate(o, st) {
    audio.unlock();
    const note = function (t) { say("info", t); sigs.msg = null; render(); };
    if (st.cls === "locked") { note(o.label + (st.tag === "locked" ? " is locked for now. Find the clues that open it." : " cannot be used right now.")); return; }
    if (st.cls === "solved") { note(o.label + " is already solved."); return; }
    if (st.cls === "occupied") { note((o.holder || "A teammate") + " is already investigating " + o.label + "."); return; }
    if (st.cls === "mine") { return; }
    draft.answer = "";
    audio.play("creak");
    act("/objects/" + o.id + "/investigate");
  }
  function submitAnswer(objectId, answer) {
    if (!answer || !answer.trim()) { say("bad", "Type an answer first."); sigs.msg = null; render(); return; }
    act("/objects/" + objectId + "/answer", { answer: answer }).then(function (res) {
      if (res && res.ok && res.data.result && res.data.result.result !== "WRONG") { draft.answer = ""; }
    });
  }
  function submitVault() {
    act("/vault", { code: draft.vault }).then(function (res) {
      if (res && res.ok && res.data.result && res.data.result.result === "CORRECT") { draft.vault = ""; }
    });
  }
  function submitDoor() {
    act("/final-door", { code: draft.door }).then(function (res) {
      if (res && res.ok && res.data.result && res.data.result.result === "CORRECT") { draft.door = ""; }
    });
  }
  function useHint(lv) {
    const ok = window.confirm("Use hint " + lv.level + "? It costs " + fmt(lv.penalty_seconds) + " of your team's time and cannot be undone.");
    if (ok) { act("/hints", { level: lv.level }); }
  }

  // ------------------------------------------------------------------ atmosphere (cosmetic only)
  function startAtmosphere() {
    (function loop() {
      setTimeout(function () {
        const stage = $("hh-stage");
        if (stage && fx() === "full" && !scareActive) {
          stage.classList.add("hh-flicker");                      // one soft dip, never a strobe
          setTimeout(function () { stage.classList.remove("hh-flicker"); }, 400);
          if (Math.random() < 0.35) {                              // now and then the room goes dark for a moment
            const dark = $("hh-dark");
            if (dark) { dark.classList.add("on"); setTimeout(function () { dark.classList.remove("on"); }, 900); }
          }
        }
        loop();
      }, 14000 + Math.random() * 22000);
    })();
  }

  // ------------------------------------------------------------------ jumpscares (the server decides; this only DISPLAYS)
  function showScare(p) {
    if (!p || !p.scare_id || seenScares[p.scare_id] || scareActive) { return; }   // dedupe: the REST reply and the socket may both carry it
    seenScares[p.scare_id] = true;
    const layer = $("hh-scare");
    if (!layer) { return; }
    scareActive = true;
    const mode = fx();
    const duration = Math.min(10000, Math.max(1000, p.duration_ms || 5000));
    layer.textContent = "";
    layer.className = "";
    if (mode === "off") {
      layer.appendChild(el("div", { class: "soft", text: "A shadow passes across the room..." }));
    } else {
      layer.appendChild(el("img", { src: p.url, alt: p.alt || "A frightening face", "data-scare": p.asset_id }));
      if (mode === "full") { layer.appendChild(el("div", { class: "flash" })); }
    }
    layer.appendChild(el("button", { type: "button", class: "skip small", text: "Dismiss", onclick: function (e) { e.stopPropagation(); endScare(); } }));
    layer.setAttribute("data-scare-id", p.scare_id);
    layer.classList.add("on");
    const game = $("hh-game");
    if (mode === "full" && game) { game.classList.add("hh-shake"); setTimeout(function () { game.classList.remove("hh-shake"); }, 500); }
    audio.play("stinger");
    showScare.timer = setTimeout(endScare, duration);
  }
  function endScare() {
    const layer = $("hh-scare");
    if (!layer || !scareActive) { return; }
    clearTimeout(showScare.timer);
    scareActive = false;
    layer.classList.add("out");
    setTimeout(function () { if (!scareActive) { layer.className = ""; layer.textContent = ""; } }, 520);
  }

  // ------------------------------------------------------------------ public API (used by player.js / realtime.js)
  HH.game = {
    start: function (code) {
      if (gameCode === code) { return; }
      gameCode = code;
      if (tickTimer) { clearInterval(tickTimer); }
      tickTimer = setInterval(tick, 500);
      refresh(false);
    },
    update: function (floorState) { setState(floorState); },
    refresh: function () { return refresh(true); },
    notify: function (text) { say("info", text); sigs.msg = null; render(); },
    setConnection: function (s) {
      connection = s;
      sigs.hud = null;
      if (state) { render(); }
      if (s === "live" && gameCode && state) { refresh(true); }   // after a drop, trust the server's state over anything shown
    },
    event: function (type, payload) {
      if (type === "JUMPSCARE") { showScare(payload); }
      else if (type === "GAME_FINISHED" && gameCode) { refresh(true); }
    }
  };
})();
