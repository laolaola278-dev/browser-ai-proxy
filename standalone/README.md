# Standalone proxies

Source snapshots of the two independent proxies (also on this machine):

- `quark/` — Quark Qwen proxy (port 8965, ut-only ticket-free URL)
- `yuanbao/` — QQ Browser Yuanbao proxy (port 8966, random device rotation)

The unified `../server.mjs` inlines both upstreams in one process; these folders are kept as reference / for separate deployment. Config files with device identifiers are intentionally excluded (see root .gitignore).
