(function () {
  const HH = window.HH;
  const el = HH.el;
  const $ = function (id) { return document.getElementById(id); };
  let gameCode = null;
  let rt = null;
  let lastSnapshot = "";

  const STATUS_TEXT = {
    WAITING_FOR_LEADER: "WAITING FOR LEADER", WAITING_FOR_PLAYERS: "WAITING FOR PLAYERS",
    READY: "READY", FULL: "FULL"
  };

  function url(suffix) { return "/api/games/" + encodeURIComponent(gameCode) + suffix; }

  async function act(method, suffix, body) {
    const res = await HH.api(url(suffix), method, body);
    if (!res.ok) { $("action-error").textContent = HH.errorText(res); return res; }
    $("action-error").textContent = "";
    render(res.data, true);
    return res;
  }

  function render(lobby, force) {
    const snapshot = JSON.stringify(lobby);
    if (!force && snapshot === lastSnapshot) { return; }
    lastSnapshot = snapshot;

    $("join-card").classList.add("hidden");
    $("lobby").classList.remove("hidden");
    const running = lobby.game_status === "RUNNING" || lobby.game_status === "FINISHED";
    $("lobby-extras").classList.toggle("hidden", running);
    $("btn-leave").classList.toggle("hidden", running);
    if (running && lobby.you.team_id !== null && HH.game) { HH.game.start(lobby.game_code); }
    $("me-name").textContent = lobby.you.display_name;
    $("game-code").textContent = lobby.game_code;
    $("lobby-state").textContent = lobby.team_formation_locked
      ? "TEAM FORMATION LOCKED" : lobby.lobby_state.replace(/_/g, " ");

    // ---- my team ----
    const myTeam = lobby.teams.find(function (t) { return t.team_id === lobby.you.team_id; });
    const box = $("my-team");
    box.textContent = "";
    if (myTeam) {
      box.classList.remove("hidden");
      box.appendChild(el("h2", { text: lobby.team_formation_locked ? "TEAM FORMATION LOCKED - your team" : "YOUR TEAM" }));
      box.appendChild(el("h3", { text: myTeam.name }));
      box.appendChild(el("div", { text: "Leader: " + (myTeam.leader_name || "-") }));
      box.appendChild(el("div", { text: "Players: " + myTeam.player_count + "/" + myTeam.max_players }));
      box.appendChild(el("div", {}, [el("span", { class: "badge " + myTeam.status, text: STATUS_TEXT[myTeam.status] || myTeam.status })]));
      box.appendChild(el("p", { class: "muted", text: "Members: " + myTeam.members.join(", ") }));
      if (lobby.you.is_leader && lobby.you.team_code) {
        box.appendChild(el("p", { text: "You are the leader. Share this Team Code with your players:" }));
        box.appendChild(el("div", { class: "code", text: lobby.you.team_code }));
      }
    } else {
      box.classList.add("hidden");
    }

    // ---- join by team code (only while formation is open and I have no team) ----
    const canJoin = lobby.lobby_state === "TEAM_FORMATION" && !lobby.you.team_id;
    $("join-team-card").classList.toggle("hidden", !canJoin);

    // ---- teams (no codes are ever shown here except my own, above) ----
    const teams = $("teams");
    teams.textContent = "";
    lobby.teams.forEach(function (t) {
      teams.appendChild(el("div", { class: "team" }, [
        el("h3", { text: t.name }),
        el("div", { text: "Leader: " + (t.leader_name || "-") }),
        el("div", { text: t.player_count + "/" + t.max_players + " players" }),
        el("div", {}, [el("span", { class: "badge " + t.status, text: STATUS_TEXT[t.status] || t.status })])
      ]));
    });

    // ---- chat ----
    const chat = $("chat");
    chat.textContent = "";
    lobby.chat.messages.forEach(function (m) {
      chat.appendChild(el("div", {}, [el("strong", { text: m.display_name + ": " }), m.body]));
    });
    chat.scrollTop = chat.scrollHeight;
    $("chat-closed").textContent = lobby.chat.closed_message || "";
    $("chat-form").classList.toggle("hidden", !lobby.chat.enabled);

    // ---- players ----
    const list = $("players");
    list.textContent = "";
    lobby.players.forEach(function (p) {
      const label = p.display_name + " - " + (p.team_name || "Unassigned") + (p.role === "LEADER" ? " (LEADER)" : "");
      list.appendChild(el("li", {}, [el("span", { class: "dot", style: "background:" + p.pawn_color }), label]));
    });
  }

  function backToJoin(message) {
    if (rt) { rt.stop(); rt = null; }
    gameCode = null;
    lastSnapshot = "";
    $("lobby").classList.add("hidden");
    $("join-card").classList.remove("hidden");
    $("join-error").textContent = message;
  }

  async function poll() {   // fallback only: used while the WebSocket is down
    if (!gameCode) { return; }
    const res = await HH.api(url("/lobby"));
    if (res.ok) { render(res.data, false); if (HH.game) { HH.game.refresh(); } return; }
    if (res.status === 401 || res.status === 403) {
      backToJoin("You are no longer in that game. Join again with a Game Code.");
    }
  }

  function start(code) {
    gameCode = code;
    if (rt) { rt.stop(); }
    rt = HH.connectRealtime({
      gameCode: code,
      role: "player",
      poll: poll,
      onState: function (state) { render(state, false); },
      onFloorState: function (floor) { if (HH.game) { HH.game.update(floor); } },
      onEvent: function (type, payload) {
        HH.logActivity($("activity"), HH.describeEvent(type, payload));
        if (HH.game && HH.game.event) { HH.game.event(type, payload); }
      },
      onStatus: function (s) {
        HH.showConnectionStatus($("conn-status"), s);
        if (HH.game && HH.game.setConnection) { HH.game.setConnection(s); }
      },
      onFatal: function (code2, message) {
        backToJoin(code2 === "KICKED" ? message : "You are no longer in that game. Join again with a Game Code.");
      }
    });
  }
  $("conn-status").addEventListener("click", function () { if (rt) { rt.retryNow(); } });

  $("join-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    const res = await HH.api("/api/games/join", "POST", {
      game_code: $("game_code").value,
      display_name: $("display_name").value
    });
    if (!res.ok) { HH.showError($("join-error"), res); return; }
    $("join-error").textContent = "";
    start(res.data.game_code);
  });

  $("btn-join-team").addEventListener("click", async function () {
    const res = await act("POST", "/teams/join", { team_code: $("team_code").value });
    if (res && res.ok) { $("team_code").value = ""; }
  });

  $("btn-chat").addEventListener("click", async function () {
    const body = $("chat_body").value;
    if (!body.trim()) { return; }
    const res = await act("POST", "/chat", { body: body });
    if (res && res.ok) { $("chat_body").value = ""; }
  });
  $("chat_body").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); $("btn-chat").click(); }
  });

  $("btn-leave").addEventListener("click", async function () {
    if (!window.confirm("Leave this lobby?")) { return; }
    const res = await HH.api(url("/leave"), "POST");
    if (res.ok) { window.location.reload(); } else { $("action-error").textContent = HH.errorText(res); }
  });

  (async function restoreSession() {
    const res = await HH.api("/api/me");
    if (res.ok && res.data.player) { start(res.data.player.game_code); }
  })();
})();
