/* Realtime client. Data flow:  server state  ->  client state (onState)  ->  UI render.
 * The server is the only source of truth: after every (re)connect it sends the full state, and
 * that state always replaces whatever the page showed before. Nothing here changes game state;
 * actions are ordinary REST calls. */
(function () {
  const HH = window.HH;

  const FATAL = { NOT_AUTHENTICATED: true, NOT_AUTHORIZED: true, KICKED: true };
  const BASE_DELAY_MS = 1000;
  const MAX_DELAY_MS = 15000;
  const MAX_ATTEMPTS = 12;       // then stop and let the user retry (no endless hammering)
  const POLL_MS = 5000;          // REST fallback while the socket is down

  HH.connectRealtime = function (opts) {
    let ws = null;
    let attempts = 0;
    let stopped = false;
    let fatal = false;
    let lastVersion = -1;
    let lastFloorVersion = -1;
    let lastDashVersion = -1;
    let retryTimer = null;
    let pollTimer = null;

    function status(s) { if (opts.onStatus) { opts.onStatus(s); } }

    function url() {
      const proto = window.location.protocol === "https:" ? "wss://" : "ws://";
      return proto + window.location.host + "/ws/games/" + encodeURIComponent(opts.gameCode) +
        "?role=" + encodeURIComponent(opts.role);
    }

    function send(obj) { if (ws && ws.readyState === 1) { ws.send(JSON.stringify(obj)); } }

    function startPolling() {
      if (!opts.poll || pollTimer) { return; }
      pollTimer = setInterval(opts.poll, POLL_MS);
    }
    function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

    function handle(msg) {
      if (msg.type === "LOBBY_STATE") {
        if (typeof msg.version === "number") {
          if (msg.version < lastVersion) { return; }   // an older snapshot overtaken by a newer one
          lastVersion = msg.version;
        }
        opts.onState(msg.payload);
      } else if (msg.type === "FLOOR_STATE") {
        if (typeof msg.version === "number") {
          if (msg.version < lastFloorVersion) { return; }
          lastFloorVersion = msg.version;
        }
        if (opts.onFloorState) { opts.onFloorState(msg.payload); }
      } else if (msg.type === "HOST_DASHBOARD") {
        if (typeof msg.version === "number") {
          if (msg.version < lastDashVersion) { return; }
          lastDashVersion = msg.version;
        }
        if (opts.onDashboard) { opts.onDashboard(msg.payload); }
      } else if (msg.type === "PING") {
        send({ type: "PONG" });
      } else if (msg.type === "PONG") {
        return;
      } else if (msg.type === "ERROR") {
        if (FATAL[msg.code]) {
          fatal = true;
          stopPolling();
          if (opts.onFatal) { opts.onFatal(msg.code, msg.message); }
        }
      } else if (opts.onEvent) {
        opts.onEvent(msg.type, msg.payload || {});
      }
    }

    function scheduleReconnect() {
      if (stopped || fatal) { return; }
      attempts += 1;
      if (attempts > MAX_ATTEMPTS) { status("failed"); return; }
      const delay = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * Math.pow(2, attempts - 1)) +
        Math.floor(Math.random() * 500);
      retryTimer = setTimeout(open, delay);
    }

    function open() {
      if (stopped || fatal) { return; }
      status(attempts ? "reconnecting" : "connecting");
      try { ws = new WebSocket(url()); } catch (e) { scheduleReconnect(); return; }
      lastVersion = -1;                                   // versions restart per connection
      lastFloorVersion = -1;
      lastDashVersion = -1;
      ws.onopen = function () { attempts = 0; stopPolling(); status("live"); };
      ws.onmessage = function (event) {
        let msg = null;
        try { msg = JSON.parse(event.data); } catch (e) { return; }
        if (msg && typeof msg.type === "string") { handle(msg); }
      };
      ws.onclose = function () {
        ws = null;
        if (stopped || fatal) { return; }
        status("offline");
        startPolling();
        scheduleReconnect();
      };
      ws.onerror = function () { /* onclose follows */ };
    }

    open();

    return {
      stop: function () {
        stopped = true;
        stopPolling();
        if (retryTimer) { clearTimeout(retryTimer); }
        if (ws) { ws.close(); }
      },
      retryNow: function () {
        if (stopped || fatal) { return; }
        attempts = 0;
        if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
        if (!ws) { open(); }
      },
      requestState: function () { send({ type: "REQUEST_STATE" }); }
    };
  };

  // ---- small helpers shared by the lobby pages ----
  const LABELS = {
    live: "LIVE", connecting: "CONNECTING...", reconnecting: "RECONNECTING...",
    offline: "OFFLINE - retrying", failed: "DISCONNECTED - click to retry"
  };
  HH.showConnectionStatus = function (el, s) {
    el.textContent = LABELS[s] || s;
    el.className = "badge conn " + s;
  };

  HH.describeEvent = function (type, p) {
    const who = p.display_name || "A player";
    const team = p.team_name || "a team";
    switch (type) {
      case "PLAYER_JOINED": return who + " joined the lobby";
      case "PLAYER_LEFT": return who + " left the lobby";
      case "PLAYER_KICKED": return who + " was removed";
      case "PLAYER_CONNECTED": return who + " connected";
      case "PLAYER_DISCONNECTED": return who + " disconnected";
      case "TEAM_JOINED": return who + " joined " + team;
      case "LEADER_ASSIGNED": return who + " is now leader of " + team;
      case "LEADER_REMOVED": return team + " lost its leader";
      case "TEAM_FORMATION_STARTED": return "Team formation started";
      case "TEAM_FORMATION_LOCKED": return "Team formation locked";
      case "TEAM_FORMATION_REOPENED": return "Team formation reopened";
      case "GAME_STARTED": return "The game has started!";
      case "OBJECT_LOCKED": return who + " is investigating " + (p.label || "an object");
      case "OBJECT_RELEASED": return (p.label || "An object") + " is free again";
      case "OBJECT_SOLVED": return (p.label || "An object") + " was solved" + (p.display_name ? " by " + p.display_name : "");
      case "ANSWER_WRONG": return who + " tried a wrong answer";
      case "CHALLENGE_PROGRESS": return who + " solved part of a challenge";
      case "VAULT_UPDATED": return "A new vault character was uncovered";
      case "VAULT_OPENED": return "The vault is open!";
      case "FRAGMENT_AWARDED": return "Key fragment collected: " + (p.fragment || "");
      case "FLOOR_COMPLETED": return "Floor " + (p.floor || "") + " complete!";
      case "QUIZ_STARTED": return who + " started the quiz";
      case "QUIZ_FINISHED": return "Quiz finished: " + p.score + "/" + p.total + (p.passed ? " (passed)" : " (not passed)");
      case "DARE_ASSIGNED": return who + " was given a dare";
      case "DARE_CONFIRMED": return who + " completed a dare";
      case "HINT_USED": return who + " used hint " + p.level + " (-" + p.penalty_seconds + "s)";
      case "TEAM_ELIMINATED": return "Time is up for " + (p.team_name || "a team");
      case "FLOOR_ENTERED": return who + " led " + team + " up to floor " + (p.floor || "?");
      case "FINAL_DOOR_OPENED": return team + " opened the final door!";
      case "GAME_FINISHED": return p.winner_team_name ? p.winner_team_name + " escaped first!" : "Time ran out for every team";
      case "JUMPSCARE": return null;      // shown as an overlay by the game view
      case "GAME_PAUSED": return "The game was paused";
      case "GAME_RESUMED": return "The game resumed";
      case "TEAM_ELIMINATED": return team + " ran out of time";
      case "FINAL_DOOR_OPENED": return team + " opened the final door";
      case "CHAT_MESSAGE": return null;   // shown in the chat box itself
      default: return null;               // TEAM_UPDATED / LOBBY_UPDATED: state already re-rendered
    }
  };

  HH.logActivity = function (listEl, text) {
    if (!text) { return; }
    listEl.insertBefore(HH.el("li", { text: text }), listEl.firstChild);
    while (listEl.children.length > 8) { listEl.removeChild(listEl.lastChild); }
  };
})();
