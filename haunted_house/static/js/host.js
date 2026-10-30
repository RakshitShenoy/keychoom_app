(function () {
  const HH = window.HH;
  const el = HH.el;
  const $ = function (id) { return document.getElementById(id); };
  let gameCode = null;
  let rt = null;
  let pending = null;   // a state that arrived while a dropdown was open
  let lastSnapshot = "";

  const STATUS_TEXT = {
    WAITING_FOR_LEADER: "WAITING FOR LEADER", WAITING_FOR_PLAYERS: "WAITING FOR PLAYERS",
    READY: "READY", FULL: "FULL"
  };

  function lobbyUrl(suffix) { return "/api/games/" + encodeURIComponent(gameCode) + suffix; }

  async function act(method, suffix, body) {
    const res = await HH.api(lobbyUrl(suffix), method, body);
    if (!res.ok) {
      HH.showError($("action-error"), res);
      const problems = res.data && res.data.error && res.data.error.details;
      if (problems && problems.length) {
        const list = $("action-error");
        problems.forEach(function (p) { list.appendChild(el("div", { text: "- " + p.message })); });
      }
      return;
    }
    $("action-error").textContent = "";
    render(res.data, true);
  }

  function leaderCandidates(lobby, team) {
    return lobby.players.filter(function (p) {
      return p.role !== "LEADER" && (p.team_id === null || p.team_id === team.team_id);
    });
  }

  function teamCard(lobby, team) {
    const kids = [
      el("h3", { text: team.name }),
      el("div", { text: "Leader: " + (team.leader_name || "-") }),
      el("div", { text: "Players: " + team.player_count + "/" + team.max_players }),
      el("div", {}, [el("span", { class: "badge " + team.status, text: STATUS_TEXT[team.status] || team.status })])
    ];
    if (team.team_code) { kids.push(el("div", { class: "teamcode", text: team.team_code })); }
    if (team.members.length) { kids.push(el("div", { class: "muted", text: team.members.join(", ") })); }

    if (lobby.lobby_state === "TEAM_FORMATION") {
      if (team.leader_player_id) {
        kids.push(el("button", {
          class: "small danger", type: "button", text: "Remove leader",
          onclick: function () { act("DELETE", "/teams/" + team.team_id + "/leader"); }
        }));
      } else {
        const select = el("select", {}, [el("option", { value: "", text: "Choose leader..." })]);
        leaderCandidates(lobby, team).forEach(function (p) {
          select.appendChild(el("option", { value: p.player_id, text: p.display_name }));
        });
        kids.push(select);
        kids.push(el("button", {
          class: "small", type: "button", text: "Assign leader",
          onclick: function () {
            if (select.value) { act("POST", "/teams/" + team.team_id + "/leader", { player_id: select.value }); }
          }
        }));
      }
    }
    return el("div", { class: "team" }, kids);
  }

  function render(lobby, force) {
    const snapshot = JSON.stringify(lobby);
    if (!force && snapshot === lastSnapshot) { return; }
    const active = document.activeElement;
    if (!force && active && active.tagName === "SELECT") {
      pending = lobby;      // don't close an open dropdown; show the newest state afterwards
      return;
    }
    pending = null;
    lastSnapshot = snapshot;

    $("create-card").classList.add("hidden");
    $("lobby").classList.remove("hidden");
    $("game-code").textContent = lobby.game_code;
    $("lobby-state").textContent = lobby.lobby_state;
    $("player-count").textContent = lobby.counts.player_count + " (need " +
      lobby.counts.min_total_players + "-" + lobby.counts.max_total_players + ", room for " +
      lobby.counts.capacity + ")";

    if ((lobby.game_status === "RUNNING" || lobby.game_status === "FINISHED") && !dash) { loadDashboard(); }
    const a = lobby.host_actions;
    $("btn-start").disabled = !a.can_start_formation;
    $("btn-lock").disabled = !a.can_lock;
    $("btn-reopen").disabled = !a.can_reopen;
    $("btn-startgame").disabled = !a.can_start_game;
    $("btn-startgame").classList.toggle("hidden", !a.can_start_game);

    const problems = $("problems");
    problems.textContent = "";
    if (!lobby.team_formation_locked) {
      lobby.readiness.problems.forEach(function (p) { problems.appendChild(el("li", { text: p.message })); });
    } else {
      problems.appendChild(el("li", { class: "closed", text: "READY TO START - team membership is locked." }));
    }

    const teams = $("teams");
    teams.textContent = "";
    lobby.teams.forEach(function (t) { teams.appendChild(teamCard(lobby, t)); });

    const table = $("players");
    table.textContent = "";
    table.appendChild(el("tr", {}, ["Player", "Team", "Role", ""].map(function (h) { return el("th", { text: h }); })));
    lobby.players.forEach(function (p) {
      table.appendChild(el("tr", {}, [
        el("td", {}, [el("span", { class: "dot", style: "background:" + p.pawn_color }), p.display_name]),
        el("td", { text: p.team_name || "Unassigned" }),
        el("td", { text: p.role }),
        el("td", {}, [el("button", {
          class: "small danger", type: "button", text: "Kick",
          onclick: function () {
            if (window.confirm("Remove " + p.display_name + " from the lobby?")) {
              act("POST", "/players/" + p.player_id + "/kick");
            }
          }
        })])
      ]));
    });

    const chat = $("chat");
    chat.textContent = "";
    lobby.chat.messages.forEach(function (m) {
      chat.appendChild(el("div", {}, [el("strong", { text: m.display_name + ": " }), m.body]));
    });
    $("chat-closed").textContent = lobby.chat.closed_message || "";
  }

  async function poll() {   // fallback only: used while the WebSocket is down
    if (!gameCode) { return; }
    const res = await HH.api(lobbyUrl("/lobby"));
    if (res.ok) { render(res.data, false); }
    if (dash) { loadDashboard(); }
  }


  // ---- Phase 10: the host's game dashboard. Server data only; pushed live over the WebSocket. ----
  let dash = null;
  let dashClockOffset = 0;
  let lastDash = "";
  function two(n) { return (n < 10 ? "0" : "") + n; }
  function fmtClock(sec) {
    const s = Math.max(0, Math.floor(sec)), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    return (h ? h + ":" + two(m) : m) + ":" + two(r);
  }
  async function loadDashboard() {   // fallback only (socket down) and the first paint after a REST action
    if (!gameCode) { return; }
    const res = await HH.api(lobbyUrl("/play/host"));
    if (res.ok) { renderDashboard(res.data); }
  }
  async function control(path) {
    const res = await HH.api(lobbyUrl(path), "POST", {});
    if (!res.ok) { $("dash-error").textContent = HH.errorText(res); return; }
    $("dash-error").textContent = "";
    renderDashboard(res.data);
  }
  function statusText(d) {
    if (d.game_status === "FINISHED") { return d.winner ? "FINISHED" : "FINISHED - NO WINNER"; }
    return d.paused ? "PAUSED" : d.game_status;
  }
  function towerRow(d, floor) {
    const here = d.teams.filter(function (t) { return t.floor === floor; });
    const chips = here.map(function (t) {
      const pct = t.room && t.room.floor_status === "COMPLETED" ? 100 : Math.round(100 * t.required_solved / Math.max(1, t.required_total));
      return el("span", { class: "chip " + t.status + (t.at_final_door ? " door" : ""), "data-team": t.name,
        title: t.where }, [
        el("span", { text: (t.icon ? t.icon + " " : "") + t.name }),
        el("i", { style: "width:" + pct + "%" })
      ]);
    });
    return el("div", { class: "tower-row" }, [
      el("div", { class: "tower-floor", text: "Floor " + floor }),
      el("div", { class: "tower-teams" }, chips.length ? chips : [el("span", { class: "muted", text: "-" })])
    ]);
  }
  function renderTower(d) {
    const box = $("tower");
    box.textContent = "";
    const escaped = d.teams.filter(function (t) { return t.status === "ESCAPED"; });
    box.appendChild(el("div", { class: "tower-row top" }, [
      el("div", { class: "tower-floor", text: "Escape" }),
      el("div", { class: "tower-teams" }, escaped.length ? escaped.map(function (t) {
        return el("span", { class: "chip ESCAPED", text: t.name });
      }) : [el("span", { class: "muted", text: "Nobody yet" })])
    ]));
    for (let f = d.total_floors; f >= 1; f--) { box.appendChild(towerRow(d, f)); }
  }
  function renderCards(d) {
    const box = $("dash-cards");
    box.textContent = "";
    d.teams.forEach(function (t) {
      const frags = el("div", { class: "frags", "aria-label": t.fragments + " of " + d.total_floors + " fragments" },
        t.fragment_flags.map(function (got, i) { return el("span", { class: got ? "got" : "", text: String(i + 1) }); }));
      const kids = [
        el("h4", { text: (t.icon ? t.icon + " " : "") + t.name }),
        el("div", { class: "badge " + t.status, text: t.status }),
        el("div", { class: "where", text: t.where }),
        el("div", { text: "Task: " + (t.task || "-") }),
        el("div", { text: "Solved on this floor: " + t.required_solved + "/" + t.required_total }),
        frags,
        el("div", { text: "Hints left " + t.hints_remaining + " - penalty " + t.penalty_seconds + "s" }),
        el("div", { class: "muted", text: "Leader: " + (t.leader || "-") + " - online " + t.connected + "/" + t.players })
      ];
      if (t.investigating.length) {
        kids.push(el("div", { class: "muted", text: "Investigating: " + t.investigating.map(function (i) { return i.label + " (" + (i.player || "?") + ")"; }).join(", ") }));
      }
      kids.push(el("div", { class: "members" }, t.members.map(function (m) {
        return el("span", { class: "member " + (m.connected ? "on" : "off") }, [
          el("span", { class: "dot", style: "background:" + m.pawn_color }), m.name + (m.is_leader ? " (leader)" : "")]);
      })));
      box.appendChild(el("div", { class: "team dash-card-team", "data-team": t.name }, kids));
    });
  }
  function renderDashboard(d) {
    dash = d;
    dashClockOffset = Date.parse(d.server_now) - Date.now();
    const key = JSON.stringify(d);
    if (key === lastDash) { return; }
    lastDash = key;
    $("dash-card").classList.remove("hidden");
    $("teams-card").classList.add("hidden");   // formation tools are finished once the game runs
    $("chat-card").classList.add("hidden");
    const st = $("dash-status");
    st.textContent = statusText(d);
    st.className = "badge dash-" + (d.paused ? "PAUSED" : d.game_status);
    $("dash-players").textContent = d.players.connected + " of " + d.players.total + " players online";
    $("dash-base").textContent = fmtClock(d.base_duration_seconds);
    $("btn-pause").disabled = !(d.game_status === "RUNNING" && !d.paused);
    $("btn-resume").disabled = !(d.game_status === "RUNNING" && d.paused);

    const box = $("dash-winner");
    box.textContent = "";
    if (d.winner) {
      box.appendChild(el("div", { class: "hh-winner", id: "dash-winner-name", text: d.winner.team_name + " escaped first!" }));
      if (d.winner.completion_seconds !== null && d.winner.completion_seconds !== undefined) {
        box.appendChild(el("div", { class: "muted", id: "dash-winner-time", text: "Completion time " + fmtClock(d.winner.completion_seconds) + " (pauses not counted). The game is over; this is the final state." }));
      }
    } else if (d.game_status === "FINISHED") {
      box.appendChild(el("div", { class: "hh-winner", text: "Time ran out. Nobody escaped." }));
    }

    renderTower(d);
    renderCards(d);

    const table = $("dash-teams");
    table.textContent = "";
    table.appendChild(el("tr", {}, ["Team", "Status", "Floor", "Fragments", "Hints left", "Penalty", "Time left", "Elapsed", "Online"].map(function (h) { return el("th", { text: h }); })));
    d.teams.forEach(function (t) {
      const live = d.game_status === "RUNNING" && t.status === "PLAYING";
      table.appendChild(el("tr", { class: d.winner && d.winner.team_id === t.team_id ? "win" : "", "data-team": t.name }, [
        el("td", { text: t.name }), el("td", {}, [el("span", { class: "badge " + t.status, text: t.status })]),
        el("td", { text: t.floor + "/" + d.total_floors }),
        el("td", { text: t.fragments + "/" + d.total_floors }), el("td", { text: String(t.hints_remaining) }),
        el("td", { text: t.penalty_seconds + "s" }),
        el("td", { class: "clock-left", "data-live": live ? "1" : "0", "data-left": String(t.seconds_remaining), text: live ? fmtClock(t.seconds_remaining) : "-" }),
        el("td", { class: "clock-elapsed", "data-live": live ? "1" : "0", "data-elapsed": String(t.elapsed_seconds || 0), text: t.elapsed_seconds === null ? "-" : fmtClock(t.elapsed_seconds) }),
        el("td", { text: t.connected + "/" + t.players })
      ]));
    });

    const feed = $("dash-feed");
    feed.textContent = "";
    d.feed.slice(0, 15).forEach(function (f) {
      const who = f.detail.display_name ? f.detail.display_name + " (" + (f.team || "?") + ") " : (f.team ? f.team + " " : "");
      const what = f.detail.label ? " - " + f.detail.label : (f.detail.floor ? " - floor " + f.detail.floor : "");
      feed.appendChild(el("li", { text: who + f.text + what }));
    });
    tickDashboard();
  }
  function tickDashboard() {   // display only: the server owns every deadline
    if (!dash) { return; }
    const sinceMs = dash.paused ? 0 : (Date.now() + dashClockOffset - Date.parse(dash.server_now));
    const since = Math.max(0, sinceMs / 1000);
    document.querySelectorAll(".clock-left[data-live='1']").forEach(function (c) {
      c.textContent = fmtClock(parseFloat(c.getAttribute("data-left")) - since);
    });
    document.querySelectorAll(".clock-elapsed[data-live='1']").forEach(function (c) {
      c.textContent = fmtClock(parseFloat(c.getAttribute("data-elapsed")) + since);
    });
    if (dash.started_at && dash.game_status === "RUNNING") {
      const base = (dash.paused ? Date.parse(dash.paused_at) : Date.now() + dashClockOffset);
      $("dash-elapsed").textContent = fmtClock((base - Date.parse(dash.started_at)) / 1000 - dash.paused_seconds_total);
    } else if (dash.started_at && dash.ended_at) {
      $("dash-elapsed").textContent = fmtClock((Date.parse(dash.ended_at) - Date.parse(dash.started_at)) / 1000 - dash.paused_seconds_total);
    }
  }
  setInterval(tickDashboard, 500);

  function start(code) {
    gameCode = code;
    if (rt) { rt.stop(); }
    rt = HH.connectRealtime({
      gameCode: code,
      role: "host",
      poll: poll,
      onState: function (state) { render(state, false); },
      onDashboard: renderDashboard,   // pushed by the server after every change; no refresh needed
      onEvent: function (type, payload) {
        HH.logActivity($("activity"), HH.describeEvent(type, payload));
      },
      onStatus: function (s) { HH.showConnectionStatus($("conn-status"), s); },
      onFatal: function () {
        $("action-error").textContent = "Your host session is no longer valid. Reload and create a new lobby.";
      }
    });
  }

  document.addEventListener("focusout", function () {
    if (pending) { setTimeout(function () { if (pending) { render(pending, true); } }, 0); }
  });
  $("conn-status").addEventListener("click", function () { if (rt) { rt.retryNow(); } });

  $("btn-start").addEventListener("click", function () { act("POST", "/team-formation/start"); });
  $("btn-lock").addEventListener("click", function () { act("POST", "/team-formation/lock"); });
  $("btn-startgame").addEventListener("click", function () {
    if (window.confirm("Start the game for every team?")) { act("POST", "/start"); }
  });
  $("btn-pause").addEventListener("click", function () { control("/pause"); });
  $("btn-resume").addEventListener("click", function () { control("/resume"); });
  $("btn-reopen").addEventListener("click", function () { act("POST", "/team-formation/reopen"); });

  $("create-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    const body = {};
    ["team_count", "min_players_per_team", "max_players_per_team"].forEach(function (field) {
      const raw = $(field).value.trim();
      if (raw !== "") { body[field] = Number(raw); }
    });
    const minutes = $("base_duration_minutes").value.trim();
    if (minutes !== "") { body.base_duration_seconds = Math.round(Number(minutes) * 60); }

    const res = await HH.api("/api/games", "POST", body);
    if (!res.ok) { HH.showError($("create-error"), res); return; }
    $("create-error").textContent = "";
    start(res.data.game_code);
  });

  (async function restoreSession() {
    const res = await HH.api("/api/me");
    if (res.ok && res.data.host) { start(res.data.host.game_code); }
  })();
})();
