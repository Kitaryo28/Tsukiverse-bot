"use strict";
/* Dashboard front-end. No inline HTML from user data: everything is built with textContent (XSS-safe). */

const app = document.getElementById("app");
let ME = null;       // {user, bot}
let S = null;        // current server payload {guild, channels, roles, config}
let TAB = "overview";
let embedDraft = null;

// ---------------------------------------------------------------- tiny helpers
function el(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === false || v == null) continue;
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "value") e.value = v;
    else if (k === "checked") e.checked = true;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return e;
}

let toastTimer;
function toast(msg, good = true) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "show " + (good ? "ok" : "bad");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = ""), good ? 2500 : 6000);
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method, credentials: "same-origin",
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch (_) { /* not json */ }
  if (res.status === 401) { showLogin(); throw new Error(data.error || "Please log in."); }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

async function run(btn, fn, okMsg) {
  btn.disabled = true;
  try { await fn(); if (okMsg) toast(okMsg); }
  catch (e) { toast(e.message, false); }
  finally { btn.disabled = false; }
}

async function save(path, body) {          // PUT -> server returns fresh payload for the whole server
  const tab = TAB;
  S = await api("PUT", `/api/guilds/${S.guild.id}${path}`, body);
  if (TAB === tab) renderTab();            // don't wipe a different tab the user already moved to
}

// ---------------------------------------------------------------- form widgets
const field = (label, control, hint) => el("label", { class: "field" }, el("span", { class: "lbl", text: label }), control, hint && el("small", { text: hint }));
const textInput = (value, ph, max) => el("input", { type: "text", value: value || "", placeholder: ph || "", maxlength: max });
const textArea = (value, ph, max, rows = 4) => el("textarea", { value: value || "", placeholder: ph || "", maxlength: max, rows });
const numInput = (value, min, max) => el("input", { type: "number", value: String(value), min: String(min), max: String(max), inputmode: "numeric" });
const button = (text, onclick, cls = "") => el("button", { class: "btn " + cls, type: "button", text, onclick });

function toggle(label, checked) {
  const input = el("input", { type: "checkbox", checked });
  const w = el("label", { class: "switch" }, input, el("span", { text: label }));
  w.input = input;
  return w;
}

function channelSelect(value, noneLabel) {
  const s = el("select");
  s.append(new Option(noneLabel || "Select a channel…", ""));
  for (const c of S.channels) s.append(new Option("# " + c.name, c.id));
  s.value = value || "";
  return s;
}

function roleSelect(value, noneLabel, assignOnly = true) {
  const s = el("select");
  s.append(new Option(noneLabel || "Select a role…", ""));
  for (const r of S.roles) {
    const o = new Option("@" + r.name + (assignOnly && !r.assignable ? "  (can't assign)" : ""), r.id);
    if (assignOnly && !r.assignable && r.id !== value) o.disabled = true;
    s.append(o);
  }
  s.value = value || "";
  return s;
}

function checkList(items, selected) {
  const box = el("div", { class: "checks" });
  if (!items.length) box.append(el("span", { class: "muted", text: "Nothing to choose from." }));
  for (const it of items) {
    const cb = el("input", { type: "checkbox", value: it.id, checked: selected.includes(it.id) });
    box.append(el("label", {}, cb, it.name));
  }
  box.values = () => [...box.querySelectorAll("input:checked")].map((i) => i.value);
  return box;
}

function actionSelect(value) {
  const s = el("select");
  for (const a of S.config.automod.actions) s.append(new Option(a, a));
  s.value = value;
  return s;
}

const lines = (text) => text.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);

// ---------------------------------------------------------------- screens
function setHeader() {
  document.getElementById("botName").textContent = ME.bot.name;
  const ba = document.getElementById("botAvatar");
  ba.src = ME.bot.avatar; ba.hidden = false;
  const who = document.getElementById("who");
  who.hidden = !ME.user;
  if (ME.user) {
    document.getElementById("userName").textContent = ME.user.name;
    document.getElementById("userAvatar").src = ME.user.avatar;
  }
}

function showLogin() {
  S = null;
  document.getElementById("who").hidden = true;
  app.replaceChildren(el("div", { class: "hero" },
    el("h1", { text: "Manage your server" }),
    el("p", { text: "Log in with Discord to set up embeds, welcome messages, reaction roles, auto-mod and more. Only people with Manage Server can open a server's dashboard." }),
    el("a", { class: "btn big", href: "/login", text: "Login with Discord" })));
}

async function showGuilds() {
  S = null;
  app.replaceChildren(el("h1", { text: "Choose a server" }), el("p", { class: "muted", text: "Loading…" }));
  const { guilds } = await api("GET", "/api/guilds");
  const cards = guilds.map((g) => el("a", { class: "card", href: `#${g.id}/overview` },
    g.icon ? el("img", { src: g.icon, alt: "" }) : el("div", { class: "ph", text: g.name.slice(0, 1).toUpperCase() }),
    el("b", { text: g.name })));
  cards.push(el("a", { class: "card", href: ME.bot.invite, target: "_blank", rel: "noopener" },
    el("div", { class: "ph", text: "+" }), el("b", { text: "Add bot to a server" })));
  app.replaceChildren(el("h1", { text: "Choose a server" }),
    el("p", { class: "muted", text: guilds.length ? "Servers where you have Manage Server and the bot is present." : "No servers found. Add the bot to a server you manage first." }),
    el("div", { class: "grid" }, cards));
}

const TABS = [
  { id: "overview", label: "Overview", render: tabOverview },
  { id: "embed", label: "Embed message", render: tabEmbed },
  { id: "welcome", label: "Welcome", render: tabWelcome },
  { id: "leveling", label: "Leveling", render: tabLeveling },
  { id: "reaction", label: "Reaction roles", render: tabReaction },
  { id: "tickets", label: "Tickets", render: tabTickets },
  { id: "automod", label: "Auto-mod", render: tabAutomod },
  { id: "autorole", label: "Auto roles", render: tabAutorole },
  { id: "triggers", label: "Triggers", render: tabTriggers },
  { id: "settings", label: "Settings", render: tabSettings },
];

async function openGuild(gid, tab) {
  TAB = TABS.some((t) => t.id === tab) ? tab : "overview";
  if (!S || S.guild.id !== gid) {
    app.replaceChildren(el("p", { class: "muted center", text: "Loading server…" }));
    try { S = await api("GET", `/api/guilds/${gid}`); }
    catch (e) { toast(e.message, false); location.hash = ""; return; }
    embedDraft = null;
  }
  app.replaceChildren(
    el("a", { href: "#", text: "← All servers" }),
    el("h1", { text: S.guild.name }),
    el("div", { class: "layout" }, el("nav", { class: "tabs", id: "tabs" }), el("section", { class: "panel", id: "panel" })));
  renderTab();
}

function renderTab() {
  const nav = document.getElementById("tabs"), panel = document.getElementById("panel");
  if (!nav || !panel) return;
  nav.replaceChildren(...TABS.map((t) => el("button", { class: t.id === TAB ? "on" : "", text: t.label, onclick: () => (location.hash = `#${S.guild.id}/${t.id}`) })));
  const active = nav.querySelector(".on");
  if (active && nav.scrollWidth > nav.clientWidth) nav.scrollLeft = active.offsetLeft - 12;   // phones: keep the current tab visible
  panel.replaceChildren();
  TABS.find((t) => t.id === TAB).render(panel);
}

// ---------------------------------------------------------------- tabs
function tabOverview(p) {
  const c = S.config, a = c.automod;
  const stat = (label, value, on) => el("div", { class: "stat" }, el("span", { class: "muted", text: label }), el("b", { class: on === undefined ? "" : on ? "on-b" : "off-b", text: value }));
  p.append(el("h2", { text: "Overview" }),
    el("div", { class: "stats" },
      stat("Prefix", c.prefix),
      stat("Welcome", c.welcome.channel ? "On" : "Off", !!c.welcome.channel),
      stat("Anti-link", a.antilink.on ? "On · " + a.antilink.action : "Off", a.antilink.on),
      stat("Bad words", a.badwords.on ? `On · ${a.badwords.words.length} words` : "Off", a.badwords.on),
      stat("Leveling", c.leveling.enabled ? `On · ${c.leveling.rewards.length} rewards` : "Off", c.leveling.enabled),
      stat("Reaction-role messages", String(c.reactionroles.length)),
      stat("Ticket panels", String(c.tickets.panels.length), c.tickets.panels.length > 0),
      stat("Triggers", String(c.triggers.length)),
      stat("Auto role (humans)", c.autorole.humans ? "Set" : "Not set", !!c.autorole.humans),
      stat("Auto role (bots)", c.autorole.bots ? "Set" : "Not set", !!c.autorole.bots)),
    el("p", { class: "muted", text: "Every change you save here is written to disk immediately, so it survives bot restarts and shutdowns." }));
}

// ---- embed
function tabEmbed(p) {
  const st = embedDraft || (embedDraft = { channel: "", content: "", use_embed: true, title: "", description: "", color: "#5865f2", image: "", thumbnail: "", footer: "", fields: [] });
  const preview = el("div", { class: "preview" });
  const draw = () => {
    preview.replaceChildren();
    if (st.content) preview.append(el("div", { class: "plain", text: st.content }));
    if (st.use_embed) {
      const e = el("div", { class: "emb" });
      e.style.borderLeftColor = st.color;
      if (st.thumbnail) e.append(el("img", { class: "th", src: st.thumbnail, alt: "" }));
      if (st.title) e.append(el("div", { class: "et", text: st.title }));
      if (st.description) e.append(el("div", { class: "ed", text: st.description }));
      if (st.fields.length) e.append(el("div", { class: "ef" }, st.fields.map((f) => el("div", {}, el("b", { text: f.name }), el("span", { text: f.value })))));
      if (st.image) e.append(el("img", { class: "ei", src: st.image, alt: "" }));
      if (st.footer) e.append(el("div", { class: "ft", text: st.footer }));
      preview.append(e);
    }
    if (!preview.children.length) preview.append(el("span", { class: "muted", text: "Preview appears here." }));
  };
  const bind = (key, input, evt = "input") => { input.addEventListener(evt, () => { st[key] = input.type === "checkbox" ? input.checked : input.value; draw(); }); return input; };

  const channel = bind("channel", channelSelect(st.channel), "change");
  const content = bind("content", textArea(st.content, "Optional normal message shown above the embed", 2000, 2));
  const useEmbed = toggle("Include an embed", st.use_embed); bind("use_embed", useEmbed.input, "change");
  const color = bind("color", el("input", { type: "color", value: st.color }));
  const fieldsBox = el("div");
  const drawFields = () => {
    fieldsBox.replaceChildren(...st.fields.map((f, i) => {
      const n = el("input", { type: "text", value: f.name, placeholder: "Field name", maxlength: 256 });
      const v = el("input", { type: "text", value: f.value, placeholder: "Field value", maxlength: 1024 });
      const inl = el("input", { type: "checkbox", checked: f.inline, title: "Inline" });
      n.addEventListener("input", () => { f.name = n.value; draw(); });
      v.addEventListener("input", () => { f.value = v.value; draw(); });
      inl.addEventListener("change", () => { f.inline = inl.checked; });
      return el("div", { class: "pair f" }, n, v, inl, button("✕", () => { st.fields.splice(i, 1); drawFields(); draw(); }, "ghost small"));
    }));
  };
  const sendBtn = button("Send message", () => run(sendBtn, async () => {
    const r = await api("POST", `/api/guilds/${S.guild.id}/embed`, st);
    toast("Sent!");
    return r;
  }));

  p.append(el("h2", { text: "Send an embed or message" }),
    el("div", { class: "cols" },
      el("div", {},
        field("Channel", channel), field("Message text", content), useEmbed,
        field("Title", bind("title", textInput(st.title, "Embed title", 256))),
        field("Description", bind("description", textArea(st.description, "Embed description (Discord **markdown** works in Discord)", 4000, 5))),
        field("Color", color),
        field("Image URL", bind("image", textInput(st.image, "https://…", 500))),
        field("Thumbnail URL", bind("thumbnail", textInput(st.thumbnail, "https://…", 500))),
        field("Footer", bind("footer", textInput(st.footer, "Footer text", 2048))),
        el("span", { class: "lbl", text: "Fields" }), fieldsBox,
        el("div", { class: "row" }, button("+ Add field", () => { if (st.fields.length < 10) { st.fields.push({ name: "", value: "", inline: false }); drawFields(); } }, "ghost small")),
        el("div", { class: "row" }, sendBtn)),
      el("div", {}, el("span", { class: "lbl", text: "Preview" }), preview)));
  drawFields(); draw();
}

// ---- welcome
function tabWelcome(p) {
  const w = S.config.welcome;
  const channel = channelSelect(w.channel, "Disabled (no welcome message)");
  const message = textArea(w.message, "Welcome {user} to **{server}**!", 1500, 4);
  const embedT = toggle("Send as an embed", w.embed);
  const title = textInput(w.title, "Embed title (optional)", 256);
  const color = el("input", { type: "color", value: w.color });
  const image = textInput(w.image, "https://… (optional)", 500);
  const footer = textInput(w.footer, "Embed footer (optional)", 2048);
  const hook = toggle("Send through a webhook (custom name & avatar)", w.webhook);
  const hname = textInput(w.webhook_name, "Webhook name", 80);
  const havatar = textInput(w.webhook_avatar, "https://… avatar link", 500);
  const saveBtn = button("Save", () => run(saveBtn, () => save("/welcome", {
    channel: channel.value, message: message.value, embed: embedT.input.checked, title: title.value, color: color.value,
    image: image.value, footer: footer.value, webhook: hook.input.checked, webhook_name: hname.value, webhook_avatar: havatar.value,
  }), "Saved"));
  const testBtn = button("Send test", () => run(testBtn, () => api("POST", `/api/guilds/${S.guild.id}/welcome/test`, {}), "Test sent (check the channel)"), "ghost");
  p.append(el("h2", { text: "Welcome message" }),
    field("Channel", channel, "Pick a channel to turn welcome messages on."),
    field("Message", message, "Placeholders: {user} (mention), {username}, {server}, {count}"),
    embedT, field("Embed title", title), field("Embed color", color), field("Embed image", image), field("Embed footer", footer),
    hook, field("Webhook name", hname), field("Webhook avatar", havatar),
    el("div", { class: "row" }, saveBtn, testBtn));
}

// ---- leveling
function tabLeveling(p) {
  const lv = S.config.leveling;
  const num = (value, min, max) => el("input", { type: "number", value: String(value), min: String(min), max: String(max), inputmode: "numeric" });
  const enabled = toggle("Leveling is on (members earn XP for chatting)", lv.enabled);
  const announce = el("select");
  const labels = { same: "In the channel they chatted in", channel: "In a specific channel", dm: "In their DMs", off: "Don't announce (silent)" };
  for (const m of lv.modes) announce.append(new Option(labels[m] || m, m));
  announce.value = lv.announce;
  const channel = channelSelect(lv.channel, "Select a channel…");
  const message = textArea(lv.message, "GG {user}, you reached level {level}!", 1500, 3);
  const embedT = toggle("Send as an embed", lv.embed);
  const title = textInput(lv.title, "Embed title (optional)", 256);
  const color = el("input", { type: "color", value: lv.color });
  const image = textInput(lv.image, "https://… (optional)", 500);
  const footer = textInput(lv.footer, "Embed footer (optional)", 2048);
  const xmin = num(lv.xp_min, 1, 500), xmax = num(lv.xp_max, 1, 500), cool = num(lv.cooldown, 0, 3600);

  // live preview of the level-up message
  const preview = el("div", { class: "preview" });
  const sample = (t) => t.replaceAll("{user}", "@Alex").replaceAll("{username}", "Alex").replaceAll("{level}", "5").replaceAll("{xp}", "1,240").replaceAll("{server}", S.guild.name);
  const draw = () => {
    preview.replaceChildren();
    if (!embedT.input.checked) { preview.append(el("div", { class: "plain", text: sample(message.value) })); return; }
    const e = el("div", { class: "emb" });
    e.style.borderLeftColor = color.value;
    if (title.value) e.append(el("div", { class: "et", text: sample(title.value) }));
    e.append(el("div", { class: "ed", text: sample(message.value) }));
    if (image.value) e.append(el("img", { class: "ei", src: image.value, alt: "" }));
    if (footer.value) e.append(el("div", { class: "ft", text: sample(footer.value) }));
    preview.append(e);
  };
  for (const i of [message, title, color, image, footer]) i.addEventListener("input", draw);
  embedT.input.addEventListener("change", draw);

  const ign = checkList(S.channels.map((c) => ({ id: c.id, name: "# " + c.name })), lv.ignored_channels);
  const ignR = checkList(S.roles.map((r) => ({ id: r.id, name: "@" + r.name })), lv.ignored_roles);
  const stack = toggle("Keep earlier rewards (off = only the highest level's role)", lv.stack);
  const rewards = lv.rewards.map((r) => ({ ...r }));
  const rbox = el("div");
  const drawRewards = () => rbox.replaceChildren(...rewards.map((r, i) => {
    const l = num(r.level || 1, 1, 500);
    const s = roleSelect(r.role);
    l.addEventListener("input", () => (r.level = l.value));
    s.addEventListener("change", () => (r.role = s.value));
    return el("div", { class: "pair", }, l, s, button("✕", () => { rewards.splice(i, 1); drawRewards(); }, "ghost small"));
  }));

  const saveBtn = button("Save", () => run(saveBtn, () => save("/leveling", {
    enabled: enabled.input.checked, announce: announce.value, channel: channel.value, message: message.value,
    embed: embedT.input.checked, title: title.value, color: color.value, image: image.value, footer: footer.value,
    xp_min: xmin.value, xp_max: xmax.value, cooldown: cool.value,
    ignored_channels: ign.values(), ignored_roles: ignR.values(),
    rewards: rewards.filter((r) => r.role), stack: stack.input.checked,
  }), "Saved"));

  p.append(el("h2", { text: "Leveling" }), enabled,
    el("div", { class: "cols" },
      el("div", {},
        field("Announce level-ups", announce), field("Announcement channel", channel, "Used when 'In a specific channel' is chosen."),
        field("Level-up message", message, "Placeholders: {user} {username} {level} {xp} {server}"),
        embedT, field("Embed title", title), field("Embed color", color), field("Embed image", image), field("Embed footer", footer)),
      el("div", {}, el("span", { class: "lbl", text: "Preview" }), preview,
        field("XP per message — min", xmin), field("XP per message — max", xmax), field("Cooldown (seconds)", cool, "A member earns XP at most once per cooldown."))),
    el("h2", { text: "Role rewards" }), stack,
    el("p", { class: "muted", text: "Level → role. The role is given when a member reaches that level." }), rbox,
    el("div", { class: "row" }, button("+ Add reward", () => { if (rewards.length < 50) { rewards.push({ level: (rewards.at(-1)?.level || 0) + 5, role: "" }); drawRewards(); } }, "ghost small")),
    el("h2", { text: "No XP in these places" }),
    el("div", { class: "cols" }, field("Channels", ign), field("Roles", ignR)),
    el("div", { class: "row" }, saveBtn));
  drawRewards(); draw();
}

// ---- reaction roles
function tabReaction(p) {
  const list = S.config.reactionroles;
  p.append(el("h2", { text: "Existing reaction roles" }));
  if (!list.length) p.append(el("p", { class: "muted", text: "None yet." }));
  for (const m of list) {
    const del = button("Remove", () => { if (confirm("Remove these reaction roles?")) run(del, async () => { const tab = TAB; S = await api("DELETE", `/api/guilds/${S.guild.id}/reactionroles/${m.message_id}`); if (TAB === tab) renderTab(); }, "Removed"); }, "danger small");
    p.append(el("div", { class: "item" },
      el("div", {}, el("a", { href: m.url, target: "_blank", rel: "noopener", text: `Message in #${m.channel_name}` }), el("br"),
        m.roles.map((r) => el("span", { class: "em" }, r.emoji_url ? el("img", { src: r.emoji_url, alt: "" }) : r.emoji, "→ @" + r.role_name))),
      del));
  }

  p.append(el("h2", { text: "Create reaction roles" }));
  const channel = channelSelect("");
  const mid = textInput("", "Leave empty to post a new embed message", 25);
  const title = textInput("Pick your roles", "Embed title", 256);
  const desc = textArea("React below to get a role.", "Embed description", 4000, 3);
  const color = el("input", { type: "color", value: "#5865f2" });
  const pairs = [{ emoji: "", role: "" }];
  const pairsBox = el("div");
  const drawPairs = () => pairsBox.replaceChildren(...pairs.map((pr, i) => {
    const e = el("input", { type: "text", value: pr.emoji, placeholder: "😀 or <:name:id>", maxlength: 64 });
    const r = roleSelect(pr.role);
    e.addEventListener("input", () => (pr.emoji = e.value));
    r.addEventListener("change", () => (pr.role = r.value));
    return el("div", { class: "pair" }, e, r, button("✕", () => { pairs.splice(i, 1); drawPairs(); }, "ghost small"));
  }));
  const create = button("Create", () => run(create, async () => {
    const body = { channel: channel.value, message_id: mid.value.trim(), pairs, embed: { title: title.value, description: desc.value, color: color.value } };
    const tab = TAB;
    S = await api("POST", `/api/guilds/${S.guild.id}/reactionroles`, body);
    if (TAB === tab) renderTab();
  }, "Reaction roles created"));
  p.append(field("Channel", channel, "Where the message is (or will be posted)."),
    field("Existing message ID (optional)", mid, "Right-click a message → Copy Message ID (Developer Mode on). If empty, I post a new embed."),
    field("New message title", title), field("New message description", desc), field("Color", color),
    el("span", { class: "lbl", text: "Emoji → role" }), pairsBox,
    el("div", { class: "row" }, button("+ Add emoji", () => { if (pairs.length < 20) { pairs.push({ emoji: "", role: "" }); drawPairs(); } }, "ghost small")),
    el("div", { class: "row" }, create));
  drawPairs();
}

// ---- auto-mod
function tabAutomod(p) {
  const a = S.config.automod;
  const alOn = toggle("Block links (message is deleted instantly)", a.antilink.on), alAct = actionSelect(a.antilink.action);
  const alMin = numInput(a.antilink.timeout_minutes, 1, 40320);
  const alGif = toggle("Allow GIFs (Tenor, Giphy, Klipy, .gif links)", a.antilink.allow_gifs);
  const alWl = textArea(a.antilink.whitelist.join("\n"), "youtube.com\nexample.org", 20000, 4);
  const bwOn = toggle("Filter bad words", a.badwords.on), bwAct = actionSelect(a.badwords.action);
  const bwMin = numInput(a.badwords.timeout_minutes, 1, 40320);
  const bwWords = textArea(a.badwords.words.join("\n"), "one word or phrase per line", 20000, 6);
  const roles = checkList(S.roles.map((r) => ({ id: r.id, name: "@" + r.name })), a.bypass_roles);
  const chans = checkList(S.channels.map((c) => ({ id: c.id, name: "# " + c.name })), a.bypass_channels);
  const saveBtn = button("Save", () => run(saveBtn, () => save("/automod", {
    antilink: { on: alOn.input.checked, action: alAct.value, timeout_minutes: alMin.value, allow_gifs: alGif.input.checked, whitelist: lines(alWl.value) },
    badwords: { on: bwOn.input.checked, action: bwAct.value, timeout_minutes: bwMin.value, words: lines(bwWords.value) },
    bypass_roles: roles.values(), bypass_channels: chans.values(),
  }), "Saved"));
  p.append(el("h2", { text: "Anti-link" }), alOn, alGif,
    field("Then also", alAct, "Pick “timeout” to mute the sender after deleting the link."),
    field("Timeout length (minutes)", alMin, "Used when the action is timeout. 60 = 1 hour, 1440 = 1 day."),
    field("Allowed domains", alWl, "One per line. Links to these sites are allowed."),
    el("h2", { text: "Bad words" }), bwOn, field("Then also", bwAct), field("Timeout length (minutes)", bwMin),
    field("Words / phrases", bwWords, "One per line (or comma-separated)."),
    el("h2", { text: "Exempt from auto-mod" }), el("div", { class: "cols" }, field("Roles", roles), field("Channels", chans)),
    el("div", { class: "row" }, saveBtn));
}

// ---- tickets
function emojiNode(text) {                       // unicode -> text, <:name:id> -> image
  const m = /^<(a?):\w+:(\d+)>$/.exec(text || "");
  if (m) return el("img", { class: "ce", alt: "", src: `https://cdn.discordapp.com/emojis/${m[2]}.${m[1] ? "gif" : "png"}?size=48` });
  return text ? el("span", { class: "ue", text }) : null;
}

function tabTickets(p) {
  const T = JSON.parse(JSON.stringify(S.config.tickets));          // working copy; nothing is saved until you press Save
  const cats = (value, none) => { const s = el("select"); s.append(new Option(none, "")); for (const c of S.categories) s.append(new Option(c.name, c.id)); s.value = value || ""; return s; };
  const bind = (obj, key, input, after, evt = "input") => { input.addEventListener(evt, () => { obj[key] = input.type === "checkbox" ? input.checked : input.value; if (after) after(); }); return input; };

  const support = roleSelect(T.support_role, "None", false), category = cats(T.category, "None (top of the channel list)");
  const logCh = channelSelect(T.log_channel, "No log channel"), maxOpen = numInput(T.max_open, 1, 10);
  const panelsBox = el("div");

  function panelCard(panel) {
    const card = el("div", { class: "tk-panel" });
    const preview = el("div", { class: "preview" });
    const buttonsBox = el("div");
    const draw = () => {
      preview.replaceChildren();
      const e = el("div", { class: "emb" });
      e.style.borderLeftColor = panel.color;
      if (panel.thumbnail) e.append(el("img", { class: "th", src: panel.thumbnail, alt: "" }));
      if (panel.title) e.append(el("div", { class: "et", text: panel.title }));
      if (panel.description) e.append(el("div", { class: "ed", text: panel.description }));
      if (panel.image) e.append(el("img", { class: "ei", src: panel.image, alt: "" }));
      if (panel.footer) e.append(el("div", { class: "ft", text: panel.footer }));
      const row = el("div", { class: "tk-btns" }, panel.buttons.map((b) => el("span", { class: "tkb " + b.style }, emojiNode(b.emoji), b.label && el("span", { text: b.label }))));
      preview.append(e, row);
    };
    const drawButtons = () => {
      buttonsBox.replaceChildren(...panel.buttons.map((b, i) => buttonEditor(panel, b, i, draw, drawButtons)));
      draw();
    };

    const channel = channelSelect(panel.channel, "Pick the panel channel…");
    const sendBtn = button(panel.message_id ? "Save & update panel message" : "Save & post panel", () => run(sendBtn, async () => {
      if (!channel.value) throw new Error("Pick the channel for this panel first.");
      await saveAll(panel.id);
    }, "Panel posted"), "");
    const del = button("Delete panel", () => {
      if (!panel.id) { T.panels.splice(T.panels.indexOf(panel), 1); drawPanels(); return; }
      if (confirm("Delete this panel and its message in Discord? Open tickets are not affected.")) {
        run(del, async () => { const tab = TAB; S = await api("DELETE", `/api/guilds/${S.guild.id}/tickets/panels/${panel.id}`); if (TAB === tab) renderTab(); }, "Panel deleted");
      }
    }, "danger small");
    bind(panel, "channel", channel, null, "change");

    card.append(
      el("h3", { text: panel.title || "Untitled panel" }),
      el("div", { class: "cols" },
        el("div", {},
          field("Panel channel", channel, panel.message_id ? "Change it and press the button below to move the panel." : "Where the panel will be posted."),
          field("Title", bind(panel, "title", textInput(panel.title, "🎫 Support", 256), draw)),
          field("Description", bind(panel, "description", textArea(panel.description, "Explain how tickets work…", 4000, 4), draw)),
          field("Color", bind(panel, "color", el("input", { type: "color", value: panel.color }), draw)),
          field("Image URL", bind(panel, "image", textInput(panel.image, "https://… (big image)", 500), draw)),
          field("Thumbnail URL", bind(panel, "thumbnail", textInput(panel.thumbnail, "https://… (small image)", 500), draw)),
          field("Footer", bind(panel, "footer", textInput(panel.footer, "Footer text", 2048), draw))),
        el("div", {}, el("span", { class: "lbl", text: "Preview" }), preview,
          panel.url && el("p", {}, el("a", { href: panel.url, target: "_blank", rel: "noopener", text: "Open the posted message" })))),
      el("span", { class: "lbl", text: "Buttons (each opens its own kind of ticket)" }), buttonsBox,
      el("div", { class: "row" },
        button("+ Add button", () => { if (panel.buttons.length < T.max_buttons) { panel.buttons.push(newTicketButton()); drawButtons(); } else toast(`At most ${T.max_buttons} buttons per panel.`, false); }, "ghost small"),
        sendBtn, del));
    drawButtons();
    return card;
  }

  function newTicketButton() {
    return { id: "", label: "Open Ticket", emoji: "🎫", style: "blurple", name: "ticket", category: "", support_role: "", ping_staff: true,
             welcome_title: "🎫 Ticket opened", welcome_message: T.default_welcome, welcome_color: "#5865f2" };
  }

  function buttonEditor(panel, b, i, draw, redraw) {
    const emojiIn = bind(b, "emoji", textInput(b.emoji, "🎫 or :name: or <:name:id>", 64), draw);
    const chips = el("div", { class: "chips" }, S.emojis.slice(0, 60).map((em) => {
      const chip = el("button", { type: "button", class: "chip", title: em.name }, el("img", { class: "ce", src: em.url, alt: em.name }));
      chip.addEventListener("click", () => { b.emoji = em.text; emojiIn.value = em.text; draw(); });
      return chip;
    }));
    const style = el("select");
    for (const s of T.styles) style.append(new Option(s, s));
    style.value = b.style;
    const head = el("summary", {}, el("span", { class: "tkb " + b.style }, emojiNode(b.emoji), el("span", { text: b.label || "(no label)" })));
    const refreshHead = () => { head.replaceChildren(el("span", { class: "tkb " + b.style }, emojiNode(b.emoji), el("span", { text: b.label || "(no label)" }))); draw(); };
    bind(b, "style", style, refreshHead, "change");
    emojiIn.addEventListener("input", refreshHead);
    const wc = bind(b, "welcome_color", el("input", { type: "color", value: b.welcome_color }));
    const body = el("div", { class: "tk-btn-body" },
      el("div", { class: "cols" },
        el("div", {}, field("Button label", bind(b, "label", textInput(b.label, "Open Ticket", 80), refreshHead)),
          field("Emoji", emojiIn, "Normal emoji, or a custom server emoji. Tap one below to use it."), S.emojis.length ? chips : el("small", { class: "muted", text: "This server has no custom emojis yet." }),
          field("Button color", style)),
        el("div", {}, field("Channel name starts with", bind(b, "name", textInput(b.name, "ticket", 30)), "e.g. “support” → support-username"),
          field("Category for these tickets", bind(b, "category", cats(b.category, "Use the default category"), null, "change")),
          field("Extra support role for this button", bind(b, "support_role", roleSelect(b.support_role, "Use the default support role", false), null, "change")),
          toggle("Ping the support role when a ticket opens", b.ping_staff))),
      el("h4", { text: "Message inside the new ticket" }),
      field("Title", bind(b, "welcome_title", textInput(b.welcome_title, "🎫 Ticket opened", 256))),
      field("Message", bind(b, "welcome_message", textArea(b.welcome_message, T.default_welcome, 1500, 4)), `You can use: ${T.placeholders}`),
      field("Color", wc),
      el("div", { class: "row" }, button("Remove button", () => { if (panel.buttons.length > 1) { panel.buttons.splice(i, 1); redraw(); } else toast("A panel needs at least one button.", false); }, "danger small")));
    // the ping toggle is the last toggle in the first column
    const pingInput = body.querySelector(".switch input"); bind(b, "ping_staff", pingInput, null, "change");
    return el("details", { class: "tk-btn" }, head, body);
  }

  function drawPanels() {
    panelsBox.replaceChildren(...T.panels.map(panelCard));
    if (!T.panels.length) panelsBox.append(el("p", { class: "muted", text: "No panels yet. Add one to get started." }));
  }

  async function saveAll(sendPid) {
    const payload = { support_role: support.value, category: category.value, log_channel: logCh.value, max_open: maxOpen.value, panels: T.panels };
    const tab = TAB;
    S = await api("PUT", `/api/guilds/${S.guild.id}/tickets`, payload);
    if (sendPid) {
      const chan = (T.panels.find((x) => x.id === sendPid) || {}).channel;
      S = await api("POST", `/api/guilds/${S.guild.id}/tickets/panels/${sendPid}/send`, { channel: chan });
    }
    if (TAB === tab) renderTab();
  }

  const saveBtn = button("Save all ticket settings", () => run(saveBtn, () => saveAll(), "Saved"));
  p.append(el("h2", { text: "Ticket settings" }),
    el("div", { class: "cols" },
      el("div", {}, field("Default support role", support, "Can see and close every ticket."), field("Default category", category, "New ticket channels are created here.")),
      el("div", {}, field("Log channel (optional)", logCh, "Opened / closed / transcript."), field("Max open tickets per member", maxOpen))),
    el("h2", { text: "Panels" }), panelsBox,
    el("div", { class: "row" },
      button("+ Add panel", () => {
        if (T.panels.length >= T.max_panels) return toast(`At most ${T.max_panels} panels.`, false);
        T.panels.push({ id: "", channel: "", message_id: "", url: "", title: "🎫 Support", description: "Need help? Press a button below to open a private ticket with our staff.",
                        color: "#5865f2", image: "", thumbnail: "", footer: "", buttons: [newTicketButton()] });
        drawPanels();
      }, "ghost"), saveBtn),
    el("p", { class: "muted", text: "“Save” stores your settings. A panel only appears in Discord when you press “Save & post panel” on it." }));
  drawPanels();
}

// ---- auto roles
function tabAutorole(p) {
  const ar = S.config.autorole;
  const humans = roleSelect(ar.humans, "None"), bots = roleSelect(ar.bots, "None");
  const saveBtn = button("Save", () => run(saveBtn, () => save("/autorole", { humans: humans.value, bots: bots.value }), "Saved"));
  p.append(el("h2", { text: "Auto roles" }), el("p", { class: "muted", text: "Given automatically when someone joins. Roles above my highest role (or yours) can't be chosen." }),
    field("Role for humans", humans), field("Role for bots", bots), el("div", { class: "row" }, saveBtn));
}

// ---- triggers
function tabTriggers(p) {
  const rows = S.config.triggers.map((t) => ({ ...t }));
  const box = el("div");
  const draw = () => box.replaceChildren(...rows.map((r, i) => {
    const ph = el("input", { type: "text", value: r.phrase, placeholder: "When someone says…", maxlength: 100 });
    const rp = el("input", { type: "text", value: r.reply, placeholder: "I reply…", maxlength: 1500 });
    ph.addEventListener("input", () => (r.phrase = ph.value));
    rp.addEventListener("input", () => (r.reply = rp.value));
    return el("div", { class: "pair t" }, ph, rp, button("✕", () => { rows.splice(i, 1); draw(); }, "ghost small"));
  }));
  const saveBtn = button("Save", () => run(saveBtn, () => save("/triggers", { triggers: rows.filter((r) => r.phrase.trim() || r.reply.trim()) }), "Saved"));
  p.append(el("h2", { text: "Triggers" }), el("p", { class: "muted", text: "Auto-replies when a message contains a whole word or phrase." }), box,
    el("div", { class: "row" }, button("+ Add trigger", () => { if (rows.length < 100) { rows.push({ phrase: "", reply: "" }); draw(); } }, "ghost small"), saveBtn));
  draw();
}

// ---- settings
function tabSettings(p) {
  const prefix = textInput(S.config.prefix, ";", 5);
  const saveBtn = button("Save", () => run(saveBtn, () => save("/prefix", { prefix: prefix.value }), "Saved"));
  p.append(el("h2", { text: "Settings" }), field("Command prefix", prefix, "1–5 characters, no spaces. You can always use the bot's @mention too."),
    el("div", { class: "row" }, saveBtn));
}

// ---------------------------------------------------------------- routing / boot
async function route() {
  if (!ME || !ME.user) return showLogin();
  const [gid, tab] = location.hash.replace(/^#/, "").split("/");
  try {
    if (!gid) await showGuilds();
    else await openGuild(gid, tab);
  } catch (e) { toast(e.message, false); }
}

document.getElementById("logout").addEventListener("click", async () => {
  try { await api("POST", "/api/logout", {}); } catch (_) { /* ignore */ }
  ME.user = null; showLogin();
});
window.addEventListener("hashchange", route);

(async function boot() {
  try { ME = await api("GET", "/api/me"); }
  catch (e) { app.replaceChildren(el("p", { class: "muted center", text: "Could not reach the dashboard server." })); return; }
  setHeader();
  route();
})();
