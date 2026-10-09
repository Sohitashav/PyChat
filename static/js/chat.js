/*
 * PyChat client — WhatsApp-style UI with groups, file sharing and PyBot.
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const layout = $("layout");
  const chatListEl = $("chat-list");
  const tabsEl = $("tabs");
  const reqBadge = $("req-badge");
  const grpBadge = $("grp-badge");
  const toastsEl = $("toasts");
  const searchInput = $("search");
  const emptyState = $("empty-state");
  const conversationEl = $("conversation");
  const messagesEl = $("messages");
  const chatAvatar = $("chat-avatar");
  const chatName = $("chat-name");
  const chatStatus = $("chat-status");
  const chatActions = $("chat-actions");
  const composerEl = $("composer");
  const textInput = $("text");
  const emojiBtn = $("emoji-btn");
  const emojiTray = $("emoji-tray");
  const backBtn = $("back-btn");
  const newChatBtn = $("new-chat-btn");
  const newGroupBtn = $("new-group-btn");
  const attachBtn = $("attach-btn");
  const fileInput = $("file-input");
  const uploadBar = $("upload-bar");
  const uploadName = $("upload-name");
  const uploadFill = $("upload-fill");
  const uploadPct = $("upload-pct");
  const dropHint = $("drop-hint");
  const addMemberBtn = $("add-member-btn");
  const leaveGroupBtn = $("leave-group-btn");
  const modal = $("modal");
  const modalTitle = $("modal-title");
  const modalIcon = $("modal-icon");
  const modalBody = $("modal-body");
  const modalOk = $("modal-ok");
  const modalCancel = $("modal-cancel");
  const modalClose = $("modal-close");

  const EMOJIS = ["😀","😂","🥰","😍","😎","🤔","😅","😢","😭","😡",
                  "👍","👎","🙏","👏","💪","❤️","💚","🔥","🎉","✨",
                  "☕","🍕","⚽","🏏","🎧","📱","💯","✅","⭐","🌈"];
  const MAX_UPLOAD = 25 * 1024 * 1024;

  let socket = null;
  let chats = [];        // conversations with accepted friends
  let incoming = [];     // friend requests waiting for my answer
  let outgoing = [];     // my friend requests, waiting for an answer
  let friends = [];      // accepted friends
  let people = [];       // last People-tab search results
  let groups = [];       // groups I belong to
  let active = null;     // currently open partner (user) or group
  let activeKind = "dm"; // "dm" | "group"
  let tab = "chats";
  let listTimer = null;
  let searchTimer = null;
  let typingTimer = null;
  let groupListTimer = null;
  let toldTyping = false;
  let theyAreTyping = false;
  let groupTyping = {};  // gid -> { name, timer }
  let modalMode = null;
  let uploadBusy = false;

  // ------------------------------------------------------------------ utils
  function fmtTime(ts) {
    return new Date(ts * 1000)
      .toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function fmtWhen(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    const today = new Date();
    if (d.toDateString() === today.toDateString()) return fmtTime(ts);
    return d.toLocaleDateString([], { day: "numeric", month: "short" });
  }

  function fmtDay(ts) {
    const d = new Date(ts * 1000);
    const today = new Date();
    const yest = new Date(today);
    yest.setDate(today.getDate() - 1);
    if (d.toDateString() === today.toDateString()) return "Today";
    if (d.toDateString() === yest.toDateString()) return "Yesterday";
    return d.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
  }

  function fmtSize(bytes) {
    if (!bytes) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  function truncate(text, n) {
    return text.length > n ? text.slice(0, n - 1) + "…" : text;
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }).then(async (res) => ({ ok: res.ok, data: await res.json() }));
  }

  function isBot(user) {
    return !!(BOT && user && user.id === BOT.id);
  }

  function makeAvatar(user) {
    const av = document.createElement("span");
    av.className = "avatar";
    av.style.background = user.avatar_color;
    av.textContent = (user.display_name || "?")[0].toUpperCase();
    return av;
  }

  function makeGroupAvatar(g, small) {
    const av = document.createElement("span");
    av.className = "avatar group-avatar" + (small ? " small" : "");
    av.style.background = `linear-gradient(135deg, ${g.avatar_color}, #00a884)`;
    av.textContent = (g.name || "#")[0].toUpperCase();
    return av;
  }

  function makeTick(status) {
    const span = document.createElement("span");
    span.className = "tick" + (status === "read" ? " read" : "");
    span.textContent = status === "sent" ? "\u2713" : "\u2713\u2713";
    return span;
  }

  function statusOf(msg) {
    if (msg.read_at) return "read";
    if (msg.delivered_at) return "delivered";
    return "sent";
  }

  function scrollBottom(force = false) {
    const near =
      messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 140;
    if (force || near) messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function toast(text) {
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = text;
    toastsEl.appendChild(el);
    setTimeout(() => el.remove(), 4500);
  }

  function beep() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.08, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.25);
      osc.connect(gain).connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.25);
      osc.onended = () => ctx.close();
    } catch (e) { /* audio blocked before first interaction */ }
  }

  // -------------------------------------------------------------- rendering
  function addDaySeparator(label) {
    const sep = document.createElement("div");
    sep.className = "day-sep";
    const pill = document.createElement("span");
    pill.textContent = label;
    sep.appendChild(pill);
    messagesEl.appendChild(sep);
  }

  function fileBlock(m) {
    const wrap = document.createElement("div");
    wrap.className = "file-block kind-" + (m.file_kind || "file");

    if (m.file_kind === "image") {
      const a = document.createElement("a");
      a.href = m.file_url;
      a.target = "_blank";
      a.rel = "noopener";
      const img = document.createElement("img");
      img.className = "msg-img";
      img.src = m.file_url;
      img.alt = m.file_name || "image";
      img.loading = "lazy";
      a.appendChild(img);
      wrap.appendChild(a);
    } else if (m.file_kind === "video") {
      const v = document.createElement("video");
      v.className = "msg-video";
      v.src = m.file_url;
      v.controls = true;
      v.preload = "metadata";
      wrap.appendChild(v);
    } else if (m.file_kind === "audio") {
      const a = document.createElement("audio");
      a.className = "msg-audio";
      a.src = m.file_url;
      a.controls = true;
      a.preload = "metadata";
      wrap.appendChild(a);
    } else {
      const a = document.createElement("a");
      a.className = "file-chip";
      a.href = m.file_url;
      a.target = "_blank";
      a.rel = "noopener";
      a.setAttribute("download", m.file_name || "");
      const ico = document.createElement("span");
      ico.className = "file-ico";
      ico.textContent = "📄";
      const meta = document.createElement("span");
      meta.className = "file-meta";
      const b = document.createElement("b");
      b.textContent = m.file_name || "file";
      const s = document.createElement("small");
      s.textContent = fmtSize(m.file_size);
      meta.append(b, s);
      a.append(ico, meta);
      wrap.appendChild(a);
    }
    return wrap;
  }

  function appendMessage(m) {
    if (m.id) {
      const dup = messagesEl.querySelector(`.bubble[data-id="${m.id}"]`);
      if (dup) return;                       // already drawn (echo / reconnect)
    }
    const isGroup = !!m.group_id;
    const mine = m.sender_id === ME.id;

    const row = document.createElement("div");
    row.className = "msg-row " + (mine ? "mine" : "theirs") +
      (isGroup ? " group-msg" : "");

    if (isGroup && !mine) {
      const av = document.createElement("span");
      av.className = "avatar msg-avatar";
      av.style.background = m.sender_color || "#553c9a";
      av.textContent = (m.sender_name || "?")[0].toUpperCase();
      row.appendChild(av);
    }

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.dataset.id = m.id;

    if (isGroup && !mine) {
      const who = document.createElement("div");
      who.className = "sender-name";
      who.textContent = m.sender_name || "";
      bubble.appendChild(who);
    }

    if (m.file_url) bubble.appendChild(fileBlock(m));

    if (m.text) {
      const body = document.createElement("div");
      body.className = "msg-text";
      body.textContent = m.text;
      bubble.appendChild(body);
    } else if (!m.file_url) {
      const body = document.createElement("div");
      body.className = "msg-text";
      body.textContent = "";
      bubble.appendChild(body);
    }

    const meta = document.createElement("div");
    meta.className = "meta";
    const time = document.createElement("span");
    time.textContent = fmtTime(m.sent_at);
    meta.appendChild(time);
    if (mine && !isGroup) meta.appendChild(makeTick(statusOf(m)));

    bubble.appendChild(meta);
    row.appendChild(bubble);
    messagesEl.appendChild(row);
  }

  function renderMessages(list) {
    messagesEl.innerHTML = "";
    let lastDay = "";
    list.forEach((m) => {
      const day = fmtDay(m.sent_at);
      if (day !== lastDay) {
        lastDay = day;
        addDaySeparator(day);
      }
      appendMessage(m);
    });
    scrollBottom(true);
  }

  function sectionLabel(text) {
    const el = document.createElement("div");
    el.className = "section-label";
    el.textContent = text;
    chatListEl.appendChild(el);
  }

  function emptyNote(text) {
    const el = document.createElement("div");
    el.className = "empty-list";
    el.textContent = text;
    chatListEl.appendChild(el);
  }

  function personRow(u, sideContent) {
    const row = document.createElement("div");
    row.className = "chat-item";
    row.dataset.user = u.id;

    const avWrap = document.createElement("div");
    avWrap.className = "av-wrap";
    avWrap.appendChild(makeAvatar(u));
    if (u.online) {
      const dot = document.createElement("span");
      dot.className = "dot";
      avWrap.appendChild(dot);
    }

    const mid = document.createElement("div");
    mid.className = "item-mid";
    const name = document.createElement("div");
    name.className = "item-name";
    name.textContent = u.display_name;
    if (isBot(u)) {
      const chip = document.createElement("span");
      chip.className = "bot-chip";
      chip.textContent = "🤖 AI";
      name.appendChild(chip);
    }
    const sub = document.createElement("div");
    sub.className = "item-preview";
    sub.textContent = isBot(u) ? "Ask me anything" : "@" + u.username;
    mid.append(name, sub);

    const right = document.createElement("div");
    right.className = "item-right";
    right.appendChild(sideContent);

    row.append(avWrap, mid, right);
    return row;
  }

  function actionBtn(label, cls, action, userId) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "row-btn " + (cls || "");
    btn.textContent = label;
    btn.dataset.action = action;
    btn.dataset.id = userId;
    return btn;
  }

  function previewNode(text, fileUrl, fileName) {
    const prev = document.createElement("div");
    prev.className = "item-preview";
    if (text) {
      prev.appendChild(document.createTextNode(truncate(text, 40)));
    } else if (fileUrl) {
      const clip = document.createElement("span");
      clip.className = "clip";
      clip.textContent = "📎 ";
      prev.appendChild(clip);
      prev.appendChild(document.createTextNode(truncate(fileName || "file", 32)));
    } else {
      prev.textContent = "Say hi \u{1F44B}";
    }
    return prev;
  }

  function renderChats() {
    chatListEl.innerHTML = "";
    if (!chats.length) {
      emptyNote("No chats yet. Open the People tab and add a friend!");
      return;
    }
    chats.forEach((c) => {
      const row = document.createElement("div");
      row.className = "chat-item" +
        (activeKind === "dm" && active && active.id === c.id ? " active" : "");
      row.dataset.user = c.id;

      const avWrap = document.createElement("div");
      avWrap.className = "av-wrap";
      avWrap.appendChild(makeAvatar(c));
      if (c.online) {
        const dot = document.createElement("span");
        dot.className = "dot";
        avWrap.appendChild(dot);
      }

      const mid = document.createElement("div");
      mid.className = "item-mid";
      const name = document.createElement("div");
      name.className = "item-name";
      name.textContent = c.display_name;
      if (isBot(c)) {
        const chip = document.createElement("span");
        chip.className = "bot-chip";
        chip.textContent = "🤖 AI";
        name.appendChild(chip);
      }
      const prev = previewNode(c.last_text, c.last_file, c.last_file_name);
      if (c.last_text && c.last_sender === ME.id) {
        prev.insertBefore(makeTick(c.last_read ? "read"
          : c.last_delivered ? "delivered" : "sent"), prev.firstChild);
      }
      mid.append(name, prev);

      const right = document.createElement("div");
      right.className = "item-right";
      if (c.last_at) {
        const when = document.createElement("div");
        when.className = "item-when" + (c.unread > 0 ? " fresh" : "");
        when.textContent = fmtWhen(c.last_at);
        right.appendChild(when);
      }
      if (c.unread > 0) {
        const badge = document.createElement("div");
        badge.className = "badge";
        badge.textContent = c.unread > 99 ? "99+" : c.unread;
        right.appendChild(badge);
      }

      row.append(avWrap, mid, right);
      chatListEl.appendChild(row);
    });
  }

  function renderGroups() {
    chatListEl.innerHTML = "";
    if (!groups.length) {
      emptyNote("No groups yet. Tap the 👥 button up top to create one!");
      return;
    }
    groups.forEach((g) => {
      const row = document.createElement("div");
      row.className = "chat-item" +
        (activeKind === "group" && active && active.id === g.id ? " active" : "");
      row.dataset.group = g.id;

      const avWrap = document.createElement("div");
      avWrap.className = "av-wrap";
      avWrap.appendChild(makeGroupAvatar(g));

      const mid = document.createElement("div");
      mid.className = "item-mid";
      const name = document.createElement("div");
      name.className = "item-name";
      name.textContent = g.name;
      const prev = document.createElement("div");
      prev.className = "item-preview";
      if (g.last_text || g.has_file) {
        const who = g.last_sender === ME.id ? "You: "
          : (g.last_sender_name ? truncate(g.last_sender_name.split(" ")[0], 10) + ": " : "");
        const whoSpan = document.createElement("span");
        whoSpan.className = "preview-who";
        whoSpan.textContent = who;
        prev.appendChild(whoSpan);
        if (g.last_text) {
          prev.appendChild(document.createTextNode(truncate(g.last_text, 34)));
        } else {
          prev.appendChild(document.createTextNode(
            (g.file_kind === "image" ? "Photo" : "📎 " + (g.last_file_name || "file"))));
        }
      } else {
        prev.textContent = "Group created — say hi!";
      }
      mid.append(name, prev);

      const right = document.createElement("div");
      right.className = "item-right";
      if (g.last_at) {
        const when = document.createElement("div");
        when.className = "item-when" + (g.unread > 0 ? " fresh" : "");
        when.textContent = fmtWhen(g.last_at);
        right.appendChild(when);
      }
      if (g.unread > 0) {
        const badge = document.createElement("div");
        badge.className = "badge";
        badge.textContent = g.unread > 99 ? "99+" : g.unread;
        right.appendChild(badge);
      }

      const meta = document.createElement("div");
      meta.className = "item-members";
      meta.textContent = `${g.member_count} member${g.member_count === 1 ? "" : "s"}`;
      right.appendChild(meta);

      row.append(avWrap, mid, right);
      chatListEl.appendChild(row);
    });
  }

  function renderRequests() {
    chatListEl.innerHTML = "";
    if (!incoming.length && !outgoing.length) {
      emptyNote("No requests. Find people in the People tab and add them!");
      return;
    }
    if (incoming.length) {
      sectionLabel("Incoming");
      incoming.forEach((r) => {
        const actions = document.createElement("div");
        actions.className = "row-actions";
        actions.append(
          actionBtn("Accept", "", "accept", r.request_id),
          actionBtn("Decline", "ghost", "decline", r.request_id),
        );
        const row = personRow(r, actions);
        row.dataset.user = r.id;
        chatListEl.appendChild(row);
      });
    }
    if (outgoing.length) {
      sectionLabel("Sent");
      outgoing.forEach((r) => {
        const btn = actionBtn("Requested…", "ghost", "noop", r.request_id);
        btn.disabled = true;
        chatListEl.appendChild(personRow(r, btn));
      });
    }
  }

  function renderPeople() {
    chatListEl.innerHTML = "";
    if (!people.length) {
      emptyNote("No users found. Ask a friend to sign up!");
      return;
    }
    people.forEach((u) => {
      let btn;
      if (u.friend_status === "friends") {
        btn = actionBtn("Message", "ghost", "open", u.id);
      } else if (u.friend_status === "pending_out") {
        btn = actionBtn("Requested…", "ghost", "noop", u.id);
        btn.disabled = true;
      } else if (u.friend_status === "pending_in") {
        const req = incoming.find((x) => x.id === u.id);
        const actions = document.createElement("div");
        actions.className = "row-actions";
        actions.append(
          actionBtn("Accept", "", "accept", req ? req.request_id : 0),
          actionBtn("Decline", "ghost", "decline", req ? req.request_id : 0),
        );
        chatListEl.appendChild(personRow(u, actions));
        return;
      } else {
        btn = actionBtn("+ Add", "", "add", u.id);
      }
      chatListEl.appendChild(personRow(u, btn));
    });
  }

  function renderCurrent() {
    if (tab === "chats") renderChats();
    else if (tab === "groups") renderGroups();
    else if (tab === "requests") renderRequests();
    else renderPeople();
  }

  function setTab(next) {
    tab = next;
    [...tabsEl.querySelectorAll(".tab")].forEach((b) =>
      b.classList.toggle("active", b.dataset.tab === next));
    renderCurrent();
  }

  function typingName() {
    if (activeKind !== "group" || !active) return "";
    const t = groupTyping[active.id];
    return t ? t.name : "";
  }

  function updateStatus() {
    chatStatus.classList.remove("online", "typing");
    if (activeKind === "group") {
      const who = typingName();
      if (who) {
        chatStatus.textContent = `${who} typing…`;
        chatStatus.classList.add("typing");
      } else {
        chatStatus.textContent =
          `${active.member_count} member${active.member_count === 1 ? "" : "s"}`;
      }
      return;
    }
    if (theyAreTyping) {
      chatStatus.textContent = "typing…";
      chatStatus.classList.add("typing");
    } else if (isBot(active)) {
      chatStatus.textContent = "AI assistant · replies instantly";
      chatStatus.classList.add("online");
    } else if (active && active.online) {
      chatStatus.textContent = "online";
      chatStatus.classList.add("online");
    } else if (active && active.last_seen) {
      chatStatus.textContent = "last seen " + fmtWhen(active.last_seen);
    } else {
      chatStatus.textContent = "offline";
    }
  }

  // ------------------------------------------------------------------ data
  async function refreshChats() {
    try {
      const res = await fetch("/api/chats");
      if (!res.ok) return;
      chats = (await res.json()).chats || [];
      if (tab === "chats") renderChats();
      updateTitle();
    } catch (e) { /* server briefly unreachable */ }
  }

  async function refreshFriends() {
    try {
      const res = await fetch("/api/friends");
      if (!res.ok) return;
      const data = await res.json();
      incoming = data.incoming || [];
      outgoing = data.outgoing || [];
      friends = data.friends || [];
      reqBadge.textContent = incoming.length;
      reqBadge.hidden = incoming.length === 0;
    } catch (e) { /* ignore */ }
  }

  async function refreshGroups() {
    try {
      const res = await fetch("/api/groups");
      if (!res.ok) return;
      groups = (await res.json()).groups || [];
      const total = groups.reduce((sum, g) => sum + (g.unread || 0), 0);
      grpBadge.textContent = total > 99 ? "99+" : total;
      grpBadge.hidden = total === 0;
      if (tab === "groups") renderGroups();
      updateTitle();
    } catch (e) { /* ignore */ }
  }

  function updateTitle() {
    const total = chats.reduce((s, c) => s + (c.unread || 0), 0) +
      groups.reduce((s, g) => s + (g.unread || 0), 0);
    document.title = total ? `(${total}) PyChat` : "PyChat";
  }

  function scheduleRefresh() {
    clearTimeout(listTimer);
    listTimer = setTimeout(refreshChats, 400);
  }

  function scheduleGroupRefresh() {
    clearTimeout(groupListTimer);
    groupListTimer = setTimeout(refreshGroups, 400);
  }

  function clearUnread(userId) {
    const c = chats.find((x) => x.id === userId);
    if (c && c.unread) {
      c.unread = 0;
      if (tab === "chats") renderChats();
      updateTitle();
    }
  }

  function clearGroupUnread(gid) {
    const g = groups.find((x) => x.id === gid);
    if (g && g.unread) {
      g.unread = 0;
      if (tab === "groups") renderGroups();
      updateTitle();
    }
  }

  async function loadPeople(query) {
    try {
      const res = await fetch("/api/users" +
        (query ? "?q=" + encodeURIComponent(query) : ""));
      const data = await res.json();
      people = data.users || [];
      if (tab === "people") renderPeople();
    } catch (e) { /* ignore */ }
  }

  async function openChat(user) {
    active = user;
    activeKind = "dm";
    theyAreTyping = false;
    emptyState.hidden = true;
    conversationEl.hidden = false;
    layout.classList.add("chat-open");
    chatActions.hidden = true;
    chatAvatar.className = "avatar";
    chatAvatar.textContent = user.display_name[0].toUpperCase();
    chatAvatar.style.background = user.avatar_color;
    chatName.textContent = user.display_name;
    if (isBot(user)) {
      const chip = document.createElement("span");
      chip.className = "bot-chip";
      chip.textContent = "🤖 AI";
      chatName.appendChild(chip);
    }
    updateStatus();
    renderCurrent();

    const res = await fetch("/api/messages/" + user.id);
    if (!res.ok) {
      toast("Send a friend request first to start chatting.");
      return;
    }
    const data = await res.json();
    renderMessages(data.messages || []);
    clearUnread(user.id);
    if (socket && socket.connected) socket.emit("read", { from: user.id });
    textInput.focus();
  }

  async function openGroup(g) {
    active = g;
    activeKind = "group";
    theyAreTyping = false;
    emptyState.hidden = true;
    conversationEl.hidden = false;
    layout.classList.add("chat-open");
    chatActions.hidden = false;
    chatAvatar.className = "avatar group-avatar";
    chatAvatar.style.background =
      `linear-gradient(135deg, ${g.avatar_color}, #00a884)`;
    chatAvatar.textContent = (g.name || "#")[0].toUpperCase();
    chatName.textContent = g.name;
    updateStatus();
    renderCurrent();

    const res = await fetch("/api/groups/" + g.id + "/messages");
    if (!res.ok) {
      toast("You are no longer in that group.");
      closeConversation();
      return;
    }
    const data = await res.json();
    active = data.group || g;
    renderMessages(data.messages || []);
    clearGroupUnread(g.id);
    if (socket && socket.connected) socket.emit("group_read", { gid: g.id });
    updateStatus();
    textInput.focus();
  }

  function closeConversation() {
    layout.classList.remove("chat-open");
    conversationEl.hidden = true;
    emptyState.hidden = false;
    active = null;
    activeKind = "dm";
    chatActions.hidden = true;
    renderCurrent();
  }

  function userById(id) {
    return chats.find((x) => x.id === id) ||
      friends.find((x) => x.id === id) ||
      people.find((x) => x.id === id) ||
      incoming.find((x) => x.id === id);
  }

  function groupById(id) {
    return groups.find((x) => x.id === id);
  }

  // ------------------------------------------------------- friend actions
  async function sendRequest(userId, btn) {
    btn.disabled = true;
    const { ok, data } = await post("/api/friends/request", { user_id: userId });
    if (data.status === "friends") {
      toast("You are now friends!");
    } else if (data.status === "pending_out" || ok) {
      toast("Friend request sent ✓");
    } else {
      toast(data.error || "Could not send request.");
    }
    await refreshFriends();
    renderCurrent();
  }

  async function respondRequest(requestId, action, name) {
    const { ok, data } = await post("/api/friends/respond", {
      request_id: requestId, action,
    });
    if (ok) {
      toast(action === "accept"
        ? `You are now friends with ${name} 🎉` : "Request declined");
      await refreshFriends();
      await refreshChats();
      renderCurrent();
    } else {
      toast(data.error || "Something went wrong.");
    }
  }

  // ------------------------------------------------------------- sidebar
  chatListEl.addEventListener("click", (event) => {
    const btn = event.target.closest("button[data-action]");
    if (btn) {
      const action = btn.dataset.action;
      if (action === "add") sendRequest(Number(btn.dataset.id), btn);
      else if (action === "accept" || action === "decline") {
        const user = userById(Number(btn.dataset.id)) ||
          incoming.find((r) => r.request_id === Number(btn.dataset.id));
        respondRequest(Number(btn.dataset.id), action,
          user ? user.display_name : "your friend");
      } else if (action === "open") {
        const u = userById(Number(btn.dataset.id));
        if (u) openChat(u);
      }
      return;
    }
    const groupRow = event.target.closest(".chat-item[data-group]");
    if (groupRow) {
      const g = groupById(Number(groupRow.dataset.group));
      if (g) openGroup(g);
      return;
    }
    const row = event.target.closest(".chat-item[data-user]");
    if (row && (tab === "chats" || tab === "people")) {
      const u = userById(Number(row.dataset.user));
      if (!u) return;
      if (tab === "people" && u.friend_status !== "friends" && u.id !== undefined) {
        if ((u.friend_status || "none") === "none") return;  // must Add first
      }
      openChat(u);
    }
  });

  tabsEl.addEventListener("click", (event) => {
    const btn = event.target.closest(".tab");
    if (!btn) return;
    setTab(btn.dataset.tab);
    if (btn.dataset.tab === "people") loadPeople(searchInput.value.trim());
    if (btn.dataset.tab === "groups") refreshGroups();
  });

  let searchSeq = 0;
  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    const q = searchInput.value.trim();
    searchTimer = setTimeout(async () => {
      const seq = ++searchSeq;
      try {
        const res = await fetch("/api/users" +
          (q ? "?q=" + encodeURIComponent(q) : ""));
        const data = await res.json();
        if (seq !== searchSeq) return;   // stale response
        people = data.users || [];
        if (q || tab === "people") setTab("people");
        else renderCurrent();
      } catch (e) { /* ignore */ }
    }, 250);
  });

  newChatBtn.addEventListener("click", () => {
    searchInput.value = "";
    searchInput.focus();
    setTab("people");
    loadPeople("");
  });

  // --------------------------------------------------------- group modal
  function openModal(mode) {
    modalMode = mode;
    modal.hidden = false;
    if (mode === "new-group") {
      modalIcon.textContent = "👥";
      modalTitle.textContent = "New group";
      modalOk.textContent = "Create group";

      const label = document.createElement("label");
      label.className = "modal-label";
      label.textContent = "Group name";
      const input = document.createElement("input");
      input.className = "modal-input";
      input.id = "group-name";
      input.maxLength = 40;
      input.placeholder = "e.g. Weekend Plans";

      const label2 = document.createElement("label");
      label2.className = "modal-label";
      label2.textContent = "Add friends";

      const picker = document.createElement("div");
      picker.className = "member-picker";
      if (!friends.length) {
        const note = document.createElement("p");
        note.className = "modal-note";
        note.textContent =
          "You need at least one friend first — find people in the People tab.";
        picker.appendChild(note);
      }
      friends.forEach((f) => {
        const row = document.createElement("label");
        row.className = "member-row";
        const box = document.createElement("input");
        box.type = "checkbox";
        box.value = f.id;
        box.className = "member-check";
        const av = makeAvatar(f);
        av.classList.add("mini");
        const nm = document.createElement("span");
        nm.className = "member-name";
        nm.textContent = f.display_name;
        const un = document.createElement("span");
        un.className = "member-user";
        un.textContent = "@" + f.username;
        row.append(box, av, nm, un);
        picker.appendChild(row);
      });

      modalBody.innerHTML = "";
      modalBody.append(label, input, label2, picker);
      setTimeout(() => input.focus(), 30);
    } else if (mode === "add-member") {
      modalIcon.textContent = "➕";
      modalTitle.textContent = "Add to " + (active ? active.name : "group");
      modalOk.textContent = "Done";

      const picker = document.createElement("div");
      picker.className = "member-picker";
      const already = active ? active.members.map((m) => m.id) : [];
      const choices = friends.filter((f) => !already.includes(f.id));
      if (!choices.length) {
        const note = document.createElement("p");
        note.className = "modal-note";
        note.textContent = "All of your friends are already here 🎉";
        picker.appendChild(note);
      }
      choices.forEach((f) => {
        const row = document.createElement("div");
        row.className = "member-row";
        const av = makeAvatar(f);
        av.classList.add("mini");
        const nm = document.createElement("span");
        nm.className = "member-name";
        nm.textContent = f.display_name;
        const un = document.createElement("span");
        un.className = "member-user";
        un.textContent = "@" + f.username;
        const add = document.createElement("button");
        add.type = "button";
        add.className = "row-btn";
        add.textContent = "+ Add";
        add.addEventListener("click", async () => {
          add.disabled = true;
          const { ok, data } = await post(
            "/api/groups/" + active.id + "/members", { user_id: f.id });
          if (ok) {
            toast(`${f.display_name} added ✓`);
            add.textContent = "Added";
            await refreshGroups();
            const fresh = groupById(active.id);
            if (fresh) { active = fresh; updateStatus(); }
          } else {
            toast(data.error || "Could not add.");
            add.disabled = false;
          }
        });
        row.append(av, nm, un, add);
        picker.appendChild(row);
      });

      modalBody.innerHTML = "";
      modalBody.appendChild(picker);
    }
  }

  function closeModal() {
    modal.hidden = true;
    modalMode = null;
  }

  modalCancel.addEventListener("click", closeModal);
  modalClose.addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModal();
  });

  modalOk.addEventListener("click", async () => {
    if (modalMode === "new-group") {
      const name = ($("group-name").value || "").trim();
      const ids = [...modalBody.querySelectorAll(".member-check:checked")]
        .map((b) => Number(b.value));
      if (!name) { toast("Give your group a name."); return; }
      if (!ids.length) { toast("Pick at least one friend."); return; }
      modalOk.disabled = true;
      const { ok, data } = await post("/api/groups",
        { name, member_ids: ids });
      modalOk.disabled = false;
      if (!ok) { toast(data.error || "Could not create the group."); return; }
      closeModal();
      await refreshGroups();
      const g = groupById(data.group.id) || data.group;
      setTab("groups");
      toast(`Group "${g.name}" created 🎉`);
      openGroup(g);
    } else {
      closeModal();
    }
  });

  newGroupBtn.addEventListener("click", () => {
    refreshFriends().then(() => openModal("new-group"));
  });

  addMemberBtn.addEventListener("click", () => {
    if (activeKind === "group") openModal("add-member");
  });

  leaveGroupBtn.addEventListener("click", async () => {
    if (activeKind !== "group" || !active) return;
    const g = active;
    if (!window.confirm(`Leave "${g.name}"?`)) return;
    const { ok, data } = await post("/api/groups/" + g.id + "/leave");
    if (ok) {
      toast(data.status === "group_deleted"
        ? "Group deleted — it was empty." : "You left the group.");
      if (activeKind === "group" && active && active.id === g.id) {
        closeConversation();
      }
      await refreshGroups();
      setTab("groups");
    } else {
      toast(data.error || "Could not leave the group.");
    }
  });

  // --------------------------------------------------------------- socket
  function wireSocket() {
    socket = io();

    socket.on("connect", () => {
      refreshFriends();
      refreshChats();
      refreshGroups();
    });

    socket.on("message", (m) => {
      if (m.recipient_id === ME.id) {
        socket.emit("delivered", { ids: [m.id] });
      }
      const fromThem = m.sender_id !== ME.id;
      const open = activeKind === "dm" && active && (fromThem
        ? m.sender_id === active.id
        : m.recipient_id === active.id);

      if (open) {
        appendMessage(m);
        scrollBottom();
        if (fromThem) {
          socket.emit("read", { from: m.sender_id });
          theyAreTyping = false;
          updateStatus();
        }
      } else if (fromThem && document.hidden) {
        beep();
      }
      scheduleRefresh();
    });

    socket.on("receipt", ({ ids, status }) => {
      (ids || []).forEach((id) => {
        const bubble = messagesEl.querySelector(`.bubble[data-id="${id}"]`);
        if (!bubble) return;
        const meta = bubble.querySelector(".meta");
        let tick = meta.querySelector(".tick");
        if (!tick) {
          tick = makeTick(status);
          meta.appendChild(tick);
        } else if (status === "read" || status === "delivered") {
          tick.className = "tick" + (status === "read" ? " read" : "");
          tick.textContent = "\u2713\u2713";
        }
      });
    });

    socket.on("typing", ({ from, typing }) => {
      if (activeKind === "dm" && active && from === active.id) {
        theyAreTyping = !!typing;
        updateStatus();
      }
    });

    socket.on("presence", ({ user_id, online, last_seen }) => {
      [chats, friends, incoming, people].forEach((list) => {
        const c = list.find((x) => x.id === user_id);
        if (c) {
          c.online = online;
          if (last_seen) c.last_seen = last_seen;
        }
      });
      if (activeKind === "dm" && active && active.id === user_id) {
        active.online = online;
        if (last_seen) active.last_seen = last_seen;
        updateStatus();
      }
      renderCurrent();
    });

    socket.on("friend_request", ({ name }) => {
      toast(`👤 ${name} sent you a friend request`);
      refreshFriends().then(renderCurrent);
    });

    socket.on("friend_accepted", ({ name }) => {
      toast(`🎉 You are now friends with ${name}`);
      refreshFriends().then(renderCurrent);
      refreshChats();
    });

    socket.on("app_error", ({ text }) => toast(text));

    // ------------------------------------------------------------ groups
    socket.on("group_message", (m) => {
      const open = activeKind === "group" && active && active.id === m.group_id;
      const fromThem = m.sender_id !== ME.id;
      if (open) {
        appendMessage(m);
        scrollBottom();
        if (fromThem) {
          socket.emit("group_read", { gid: m.group_id });
          delete groupTyping[m.group_id];
          updateStatus();
        }
      } else if (fromThem && document.hidden) {
        beep();
      }
      scheduleGroupRefresh();
    });

    socket.on("group_typing", ({ gid, from, name, typing }) => {
      if (from === ME.id) return;
      if (typing) {
        if (groupTyping[gid]) clearTimeout(groupTyping[gid].timer);
        groupTyping[gid] = {
          name,
          timer: setTimeout(() => { delete groupTyping[gid]; updateStatus(); }, 3000),
        };
      } else if (groupTyping[gid]) {
        clearTimeout(groupTyping[gid].timer);
        delete groupTyping[gid];
      }
      if (activeKind === "group" && active && active.id === gid) updateStatus();
    });

    socket.on("group_read", ({ gid, user_id }) => {
      if (user_id === ME.id) clearGroupUnread(gid);
    });

    socket.on("group_update", ({ group }) => {
      const i = groups.findIndex((g) => g.id === group.id);
      const stillMine = group.members.some((m) => m.id === ME.id);
      if (!stillMine) {
        if (i >= 0) groups.splice(i, 1);
        if (activeKind === "group" && active && active.id === group.id) {
          closeConversation();
        }
      } else if (i >= 0) {
        groups[i] = group;
        if (activeKind === "group" && active && active.id === group.id) {
          active = group;
          updateStatus();
        }
      } else {
        groups.unshift(group);
      }
      if (tab === "groups") renderGroups();
      updateTitle();
    });

    socket.on("group_created", ({ name }) => {
      toast(`👥 You created "${name}"`);
      refreshGroups();
    });

    socket.on("group_invite", ({ name }) => {
      toast(`👥 You were added to "${name}"`);
      refreshGroups();
    });
  }

  // ------------------------------------------------------------- composer
  composerEl.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = textInput.value.trim();
    if (!text || !active || !socket.connected) return;
    if (activeKind === "group") {
      socket.emit("group_message", { gid: active.id, text });
    } else {
      socket.emit("message", { to: active.id, text });
    }
    textInput.value = "";
    emojiTray.hidden = true;
    stopTyping();
    textInput.focus();
  });

  function stopTyping() {
    clearTimeout(typingTimer);
    if (!toldTyping || !active) return;
    toldTyping = false;
    if (!socket.connected) return;
    if (activeKind === "group") socket.emit("group_typing", { gid: active.id, typing: false });
    else socket.emit("typing", { to: active.id, typing: false });
  }

  textInput.addEventListener("input", () => {
    if (!active || !socket.connected) return;
    if (!toldTyping) {
      toldTyping = true;
      if (activeKind === "group") socket.emit("group_typing", { gid: active.id, typing: true });
      else socket.emit("typing", { to: active.id, typing: true });
    }
    clearTimeout(typingTimer);
    typingTimer = setTimeout(stopTyping, 1600);
  });

  // ------------------------------------------------------------ uploads
  function showUpload(name) {
    uploadBusy = true;
    uploadBar.hidden = false;
    uploadName.textContent = name;
    uploadFill.style.width = "0%";
    uploadPct.textContent = "0%";
  }

  function hideUpload() {
    uploadBusy = false;
    uploadBar.hidden = true;
  }

  function uploadFile(file) {
    return new Promise((resolve) => {
      if (!file || !active) { resolve(); return; }
      if (file.size > MAX_UPLOAD) {
        toast("That file is over 25 MB — pick something smaller.");
        resolve();
        return;
      }
      if (uploadBusy) {
        toast("Please wait — the previous file is still sending.");
        resolve();
        return;
      }
      showUpload(file.name);

      const form = new FormData();
      form.append("file", file);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/upload");
      xhr.upload.onprogress = (e) => {
        if (!e.lengthComputable) return;
        const pct = Math.round((e.loaded / e.total) * 100);
        uploadFill.style.width = pct + "%";
        uploadPct.textContent = pct + "%";
      };
      xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch (e) { /* bad json */ }
        hideUpload();
        if (xhr.status !== 200 || data.error) {
          toast(data.error || "Upload failed.");
          resolve();
          return;
        }
        const payload = {
          file_url: data.url, file_name: data.name,
          file_size: data.size, file_kind: data.kind, text: "",
        };
        if (activeKind === "group") socket.emit("group_message", { gid: active.id, ...payload });
        else socket.emit("message", { to: active.id, ...payload });
        toast("📎 " + data.name + " sent");
        resolve();
      };
      xhr.onerror = () => {
        hideUpload();
        toast("Upload failed — is the server running?");
        resolve();
      };
      xhr.send(form);
    });
  }

  attachBtn.addEventListener("click", () => {
    if (!active) { toast("Open a chat first."); return; }
    fileInput.click();
  });

  fileInput.addEventListener("change", () => {
    const file = fileInput.files && fileInput.files[0];
    if (file) uploadFile(file);
    fileInput.value = "";
  });

  // drag & drop straight onto the conversation
  let dragDepth = 0;
  ["dragenter", "dragover"].forEach((type) => {
    conversationEl.addEventListener(type, (e) => {
      if (!e.dataTransfer || ![...e.dataTransfer.types].includes("Files")) return;
      e.preventDefault();
      if (type === "dragenter") dragDepth++;
      dropHint.hidden = false;
    });
  });
  conversationEl.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) dropHint.hidden = true;
  });
  conversationEl.addEventListener("drop", async (e) => {
    if (!e.dataTransfer || !e.dataTransfer.files.length) return;
    e.preventDefault();
    dragDepth = 0;
    dropHint.hidden = true;
    const files = [...e.dataTransfer.files];
    if (files.length > 1) toast(`Sending ${files.length} files…`);
    for (const file of files) {
      await uploadFile(file);          // one at a time, uploadFile waits
    }
  });

  // ---------------------------------------------------------------- emoji
  EMOJIS.forEach((emo) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = emo;
    b.addEventListener("click", () => {
      textInput.value += emo;
      textInput.focus();
    });
    emojiTray.appendChild(b);
  });
  emojiBtn.addEventListener("click", () => { emojiTray.hidden = !emojiTray.hidden; });

  backBtn.addEventListener("click", closeConversation);

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.hidden) closeModal();
  });

  // ----------------------------------------------------------------- start
  wireSocket();
  refreshFriends();
  refreshChats();
  refreshGroups();
})();
