// Runs inside Discord's own web client (hidden). Reads friends, presence and
// direct messages from Discord's stores and sends through Discord's own
// actions, so the traffic is exactly the web client's. Talks to the
// Messenger UI with console messages prefixed "HMSG" (out) and
// window.__hm.cmd(...) (in).
(() => {
    if (window.__hm) return;
    const out = (type, data) => console.log("HMSG" + JSON.stringify({ type, data }));
    let req = null;

    function getReq() {
        if (req) return req;
        const ch = window.webpackChunkdiscord_app;
        if (!ch || !ch.push) return null;
        ch.push([[Symbol("halcyon")], {}, (r) => { req = r; }]);
        try { ch.pop(); } catch (e) {}
        return req;
    }

    function loaders() {
        // Every module loader hook.js caught (Discord runs more than one), plus the classic one.
        const all = (window.__hmReqs || []).filter((r) => r.c);
        const r = getReq();
        if (r && r.c && !all.includes(r)) all.push(r);
        return all;
    }

    function* exportsAll() {
        for (const r of loaders()) yield* exportsOf(r);
    }

    function* exportsOf(r) {
        for (const id in r.c) {
            let m;
            try { m = r.c[id] && r.c[id].exports; } catch (e) { continue; }
            if (!m || (typeof m !== "object" && typeof m !== "function")) continue;
            yield m;
            if (typeof m === "object") {
                for (const k of Object.keys(m)) {
                    let v;
                    try { v = m[k]; } catch (e) { continue; }
                    if (v && (typeof v === "object" || typeof v === "function")) yield v;
                }
            }
        }
    }

    const cache = {};
    // Some exports (Discord's translations) answer to any property name; never match those.
    function catchAll(e) {
        try { return e["__halcyon_no_such_prop__"] !== undefined; } catch (err) { return true; }
    }
    function byProps(...props) {
        const key = "p:" + props.join(",");
        if (cache[key]) return cache[key];
        for (const e of exportsAll()) {
            try {
                if (props.every((p) => e[p] !== undefined) && !catchAll(e)) return (cache[key] = e);
            } catch (err) {}
        }
        return null;
    }
    function store(name) {
        const key = "s:" + name;
        if (cache[key]) return cache[key];
        for (const e of exportsAll()) {
            try {
                if (typeof e.getName === "function" && e._dispatchToken !== undefined && !catchAll(e)
                    && e.getName() === name)
                    return (cache[key] = e);
            } catch (err) {}
        }
        return null;
    }

    const S = {};
    function stores() {
        for (const n of ["UserStore", "RelationshipStore", "PresenceStore", "ChannelStore", "MessageStore",
                         "PrivateChannelSortStore", "SelectedChannelStore", "UserProfileStore", "SessionsStore",
                         "GuildStore", "SortedGuildStore", "GuildChannelStore", "ReadStateStore"])
            if (!S[n]) S[n] = store(n);
        return S;
    }

    // --- shaping data for the UI
    function avatar(u, size) {
        try { return u.getAvatarURL(undefined, size || 128, false); } catch (e) { return null; }
    }
    function displayName(u) {
        if (!u) return "Unknown";
        try {
            const nick = S.RelationshipStore && S.RelationshipStore.getNickname && S.RelationshipStore.getNickname(u.id);
            if (nick) return nick;
        } catch (e) {}
        return u.globalName || u.global_name || u.username;
    }
    function presence(id) {
        let status = "offline", activities = [];
        try { status = S.PresenceStore.getStatus(id) || "offline"; } catch (e) {}
        try { activities = S.PresenceStore.getActivities(id) || []; } catch (e) {}
        const custom = activities.find((a) => a.type === 4);
        const playing = activities.find((a) => a.type === 0 || a.type === 2);
        return {
            status,
            psm: custom ? [custom.emoji && !custom.emoji.id ? custom.emoji.name : "", custom.state || ""].join(" ").trim() : "",
            playing: playing ? (playing.type === 2 ? "♫ " + [playing.details, playing.state].filter(Boolean).join(" - ")
                                               : "Playing " + playing.name) : "",
        };
    }
    function contact(u) {
        return Object.assign({ id: u.id, name: displayName(u), username: u.username, avatar: avatar(u, 128) },
                             presence(u.id));
    }
    function me() {
        const u = S.UserStore.getCurrentUser();
        const c = contact(u);
        // Our own status comes from the session, not presence.
        try {
            const st = S.SessionsStore && S.SessionsStore.getActiveSession && S.SessionsStore.getActiveSession();
            if (st && typeof st.status === "string") c.status = st.status;
        } catch (e) {}
        if (typeof c.status !== "string") c.status = "online";
        return c;
    }
    function friends() {
        const R = S.RelationshipStore;
        let ids = [];
        try { ids = R.getFriendIDs(); } catch (e) {
            try {
                const rel = (R.getMutableRelationships && R.getMutableRelationships()) || R.getRelationships();
                ids = [...(rel instanceof Map ? rel.entries() : Object.entries(rel))]
                    .filter(([, t]) => t === 1).map(([id]) => id);
            } catch (e2) {}
        }
        return ids.map((id) => S.UserStore.getUser(id)).filter(Boolean).map(contact);
    }
    function groups() {
        let chans = [];
        try { chans = S.ChannelStore.getSortedPrivateChannels(); } catch (e) {}
        return chans.filter((c) => c.type === 3).map((c) => ({
            id: c.id,
            name: c.name || (c.recipients || []).map((r) => displayName(S.UserStore.getUser(r))).join(", "),
            members: (c.recipients || []).length + 1,
        }));
    }
    function recentDMs() {
        let chans = [];
        try { chans = S.ChannelStore.getSortedPrivateChannels(); } catch (e) {}
        const out = [];
        for (const c of chans) {
            if (c.type !== 1) continue;
            const u = S.UserStore.getUser((c.recipients || [])[0]);
            if (u) out.push(Object.assign(contact(u), { lastMessageId: c.lastMessageId || c.last_message_id || "0" }));
            if (out.length >= 40) break;
        }
        return out;
    }
    function guilds() {
        let ids = [];
        try { ids = S.SortedGuildStore.getFlattenedGuildIds(); } catch (e) {}
        if (!ids || !ids.length) {
            try { ids = Object.keys(S.GuildStore.getGuilds()); } catch (e) { ids = []; }
        }
        return ids.map((id) => {
            let g = null;
            try { g = S.GuildStore.getGuild(id); } catch (e) {}
            if (!g) return null;
            let icon = null;
            try { icon = g.icon ? `https://cdn.discordapp.com/icons/${g.id}/${g.icon}.png?size=128` : null; } catch (e) {}
            return { id: g.id, name: g.name, icon };
        }).filter(Boolean);
    }
    function guildChannels(guildId) {
        let list = [];
        try {
            const all = S.GuildChannelStore.getChannels(guildId);
            list = (all.SELECTABLE || []).map((x) => x.channel || x);
        } catch (e) {}
        // Text-like channels only: text (0), announcements (5), forums are skipped.
        return list.filter((c) => c.type === 0 || c.type === 5).map((c) => {
            let unread = false;
            try { unread = S.ReadStateStore.hasUnread(c.id); } catch (e) {}
            let parent = "", parentPos = -1;
            try {
                const p = c.parent_id && S.ChannelStore.getChannel(c.parent_id);
                if (p) { parent = p.name; parentPos = p.position; }
            } catch (e) {}
            return { id: c.id, name: c.name, topic: c.topic || "", unread, category: parent,
                     categoryId: c.parent_id || "", categoryPos: parentPos, pos: c.position || 0 };
        });
    }
    const watched = new Set();

    function media(x) {
        if (!x) return null;
        const url = x.proxy_url || x.proxyURL || x.url;
        return url ? { url, width: x.width || 0, height: x.height || 0 } : null;
    }
    function embed(e) {
        // Store records and raw gateway events name things differently.
        const color = typeof e.color === "number" ? "#" + e.color.toString(16).padStart(6, "0") : (e.color || "");
        return {
            type: e.type || "rich",
            url: e.url || "",
            title: e.rawTitle || e.title || "",
            description: e.rawDescription || e.description || "",
            color,
            provider: e.provider ? e.provider.name || "" : "",
            author: e.author ? e.author.name || "" : "",
            thumbnail: media(e.thumbnail),
            image: media(e.image) || (e.images && e.images.length ? media(e.images[0]) : null),
            video: media(e.video),
            fields: (e.fields || []).slice(0, 10).map((f) => ({ name: f.rawName || f.name || "",
                                                                  value: f.rawValue || f.value || "" })),
            footer: e.footer ? e.footer.text || "" : "",
        };
    }

    function message(m, channelId) {
        const a = m.author || {};
        return {
            id: m.id, channelId: channelId || m.channel_id || m.channelId,
            author: a.id, authorName: displayName(S.UserStore.getUser(a.id) || a),
            content: m.content || "",
            time: new Date(m.timestamp).getTime() || Date.now(),
            attachments: (m.attachments || []).map((x) => ({
                name: x.filename, url: x.url, proxy: x.proxy_url || x.proxyURL || x.url,
                type: x.content_type || x.contentType || "", width: x.width || 0, height: x.height || 0 })),
            embeds: (m.embeds || []).map(embed),
            stickers: (m.stickerItems || m.sticker_items || []).map((s) => s.name),
            mine: a.id === S.UserStore.getCurrentUser().id,
        };
    }
    function isPrivate(channelId) {
        try { const c = S.ChannelStore.getChannel(channelId); return c && (c.type === 1 || c.type === 3); }
        catch (e) { return false; }
    }
    function peerOf(channelId) {
        try {
            const c = S.ChannelStore.getChannel(channelId);
            if (c.type === 1) return (c.recipients || [])[0];
        } catch (e) {}
        return null;
    }

    // --- actions
    const A = {};
    function actions() {
        A.msg = A.msg || byProps("sendMessage", "editMessage", "fetchMessages");
        // Discord's API client is {get, post, put, patch, del}; the HTTP library under it
        // has the same verbs plus Request, and doesn't sign requests.
        if (!A.rest) {
            for (const e of exportsAll()) {
                try {
                    if (["get", "post", "put", "patch", "del"].every((k) => typeof e[k] === "function")
                        && e.Request === undefined && e.agent === undefined && !catchAll(e)) { A.rest = e; break; }
                } catch (err) {}
            }
        }
        A.typing = A.typing || byProps("startTyping", "stopTyping");
        // Settings controllers: type 1 = main settings (status), type 2 = frecency (favorite GIFs).
        if (!A.settings || !A.frecency) {
            for (const e of exportsAll()) {
                try {
                    if (typeof e.updateAsync === "function" && typeof e.loadIfNecessary === "function"
                        && typeof e.getCurrentValue === "function" && !catchAll(e)) {
                        if (e.type === 1) A.settings = e;
                        if (e.type === 2) A.frecency = e;
                    }
                } catch (err) {}
                if (A.settings && A.frecency) break;
            }
        }
        return A;
    }
    async function openDM(userId) {
        let id = null;
        try { id = S.ChannelStore.getDMFromUserId(userId); } catch (e) {}
        if (id) return id;
        const r = await actions().rest.post({ url: "/users/@me/channels", body: { recipients: [userId] } });
        return r.body.id;
    }
    async function history(channelId) {
        try { await actions().msg.fetchMessages({ channelId, limit: 30 }); } catch (e) {}
        let list = [];
        try {
            const ms = S.MessageStore.getMessages(channelId);
            list = (ms.toArray ? ms.toArray() : ms._array || []).slice(-30);
        } catch (e) {}
        return list.map((m) => message(m, channelId));
    }
    async function setStatus(st) {
        const P = actions().settings;
        await P.updateAsync("status", (s) => { s.status = { value: st }; }, 0);
    }
    async function setPsm(text) {
        const P = actions().settings;
        await P.updateAsync("status", (s) => {
            s.customStatus = text ? { text, emojiId: "0", emojiName: "", expiresAtMs: "0",
                                      createdAtMs: String(Date.now()) } : undefined;
        }, 0);
    }

    const commands = {
        open: async ({ userId }) => {
            const channelId = await openDM(userId);
            return { channelId, messages: await history(channelId) };
        },
        openGroup: async ({ channelId }) => ({ channelId, messages: await history(channelId) }),
        send: async ({ channelId, text }) => {
            await actions().msg.sendMessage(channelId, { content: text, tts: false, invalidEmojis: [],
                                                         validNonShortcutEmojis: [] }, undefined, {});
            return true;
        },
        typing: async ({ channelId }) => { actions().typing.startTyping(channelId); return true; },
        status: async ({ status }) => { await setStatus(status); return true; },
        psm: async ({ text }) => { await setPsm(text); return true; },
        older: async ({ channelId, before }) => {
            const r = await actions().rest.get({ url: `/channels/${channelId}/messages`, query: { before, limit: 50 } });
            return (r.body || []).reverse().map((m) => message(m, channelId));
        },
        favGifs: async () => {
            const F = actions().frecency;
            await F.loadIfNecessary();
            const cur = F.getCurrentValue();
            const gifs = (cur && cur.favoriteGifs && cur.favoriteGifs.gifs) || {};
            return Object.entries(gifs).sort((a, b) => (b[1].order || 0) - (a[1].order || 0))
                .map(([url, g]) => ({ url, src: g.src, video: g.format === 2 || /\.(mp4|webm)(\?|$)|\/mp4$/.test(g.src || ""),
                                      width: g.width, height: g.height }));
        },
        gifs: async ({ q }) => {
            const query = { media_format: "mp4", provider: "tenor", locale: "en-US" };
            const r = q ? await actions().rest.get({ url: "/gifs/search", query: Object.assign({ q }, query) })
                        : await actions().rest.get({ url: "/gifs/trending-gifs", query });
            return (r.body || []).map((g) => ({ url: g.url, gif: g.gif_src, mp4: g.src, title: g.title || "",
                                                width: g.width, height: g.height }));
        },
        snapshot: async () => ({ me: me(), friends: friends(), groups: groups(), guilds: guilds(), recent: recentDMs() }),
        channels: async ({ guildId }) => guildChannels(guildId),
        watch: async ({ channelId, on }) => { on ? watched.add(channelId) : watched.delete(channelId); return true; },
        probe: async () => {
            const a = actions(), s = stores();
            return { stores: Object.fromEntries(Object.entries(s).map(([k, v]) => [k, !!v])),
                     actions: Object.fromEntries(Object.entries(a).map(([k, v]) => [k, !!v])) };
        },
    };

    window.__hm = {
        cmd(rid, name, args) {
            Promise.resolve().then(() => commands[name](args || {}))
                .then((result) => out("reply", { rid, ok: true, result }))
                .catch((e) => out("reply", { rid, ok: false, error: String(e && e.message || e) }));
        },
    };

    // --- events
    let started = false;
    let snapTimer = null;
    function snapshotSoon() {
        clearTimeout(snapTimer);
        snapTimer = setTimeout(() => out("snapshot", { me: me(), friends: friends(), groups: groups(),
                                                        guilds: guilds(), recent: recentDMs() }), 300);
    }
    function start() {
        if (started) return;
        const D = byProps("dispatch", "subscribe", "unsubscribe", "register");
        stores();
        if (!D || !S.UserStore || !S.UserStore.getCurrentUser() || !S.RelationshipStore || !S.PresenceStore) return;
        started = true;
        D.subscribe("MESSAGE_CREATE", (e) => {
            if (e.optimistic) return;
            if (isPrivate(e.channelId)) {
                const m = message(e.message, e.channelId);
                m.peer = peerOf(e.channelId);
                out("message", m);
                return;
            }
            // Server channels: only ones open in a window, or messages that mention me.
            const myId = S.UserStore.getCurrentUser().id;
            const mentioned = (e.message.mentions || []).some((u) => (u.id || u) === myId);
            if (!watched.has(e.channelId) && !mentioned) return;
            const m = message(e.message, e.channelId);
            m.guild = e.guildId || e.message.guild_id || null;
            m.mentioned = mentioned;
            try { m.channelName = S.ChannelStore.getChannel(e.channelId).name; } catch (err) {}
            try { m.guildName = S.GuildStore.getGuild(m.guild).name; } catch (err) {}
            out("message", m);
        });
        D.subscribe("TYPING_START", (e) => {
            if ((!isPrivate(e.channelId) && !watched.has(e.channelId)) || e.userId === S.UserStore.getCurrentUser().id) return;
            out("typing", { channelId: e.channelId, userId: e.userId,
                            name: displayName(S.UserStore.getUser(e.userId)) });
        });
        D.subscribe("PRESENCE_UPDATES", (e) => {
            for (const u of e.updates || []) {
                const user = S.UserStore.getUser(u.user && u.user.id);
                if (user) out("presence", contact(user));
            }
        });
        for (const t of ["RELATIONSHIP_ADD", "RELATIONSHIP_REMOVE", "RELATIONSHIP_UPDATE", "CHANNEL_CREATE",
                         "CHANNEL_DELETE", "CURRENT_USER_UPDATE", "USER_SETTINGS_PROTO_UPDATE"])
            D.subscribe(t, snapshotSoon);
        for (const t of ["GUILD_CREATE", "GUILD_DELETE", "GUILD_UPDATE"]) D.subscribe(t, snapshotSoon);
        out("ready", { me: me(), friends: friends(), groups: groups(), guilds: guilds(), recent: recentDMs() });
    }
    let tries = 0;
    const poll = setInterval(() => {
        try { start(); } catch (e) { out("error", String(e && e.stack || e)); }
        if (started) { clearInterval(poll); return; }
        if (location.pathname.startsWith("/login") || location.pathname.startsWith("/register")) { out("login", {}); return; }
        // Signed in but not started: say what's missing, now and then.
        if (++tries % 10 === 5) {
            const r = getReq();
            let user = null;
            try { user = !!(S.UserStore && S.UserStore.getCurrentUser()); } catch (e) {}
            out("waiting", {
                path: location.pathname, chunk: !!window.webpackChunkdiscord_app, req: !!r,
                modules: r && r.c ? Object.keys(r.c).length : 0,
                loaders: loaders().map((l) => Object.keys(l.c).length),
                factories: r && r.m ? Object.keys(r.m).length : 0,
                globals: Object.keys(window).filter((k) => /webpack|chunk/i.test(k)),
                chunkLen: window.webpackChunkdiscord_app ? window.webpackChunkdiscord_app.length : 0,
                dispatcher: !!byProps("dispatch", "subscribe", "unsubscribe", "register"),
                dispatcherAlt: !!byProps("dispatch", "subscribe"),
                stores: Object.fromEntries(Object.entries(stores()).map(([k, v]) => [k, !!v])), user,
            });
        }
    }, 1000);
})();
