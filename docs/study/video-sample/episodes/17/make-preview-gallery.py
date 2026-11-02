#!/usr/bin/env python3
"""Build a local, silent episode-17 screen gallery from existing PNG previews."""
from collections import Counter
from html import escape
import json
from pathlib import Path

EPISODE = Path(__file__).resolve().parent
BASE = EPISODE.parent.parent
OUTPUT = BASE / 'output' / 'lesson-17-gemini' / 'previews'
script = json.loads((EPISODE / 'script.json').read_text())
turns = script['turns']
chapters = script['chapters']
assert len(turns) == 75, 'Expected the reviewed 75-turn episode.'
assert len({turn['id'] for turn in turns}) == len(turns)
assert all((OUTPUT / f"{turn['id']}.png").is_file() for turn in turns)
counts = Counter(turn['chapterId'] for turn in turns)

def h(value):
    return escape(str(value), quote=True)

nav = ''.join(f'<a href="#chapter-{h(c["id"])}">{i}. {h(c["title"])} <span>{counts[c["id"]]}場面</span></a>'
              for i, c in enumerate(chapters, 1))
sections = []
for n, chapter in enumerate(chapters, 1):
    articles = []
    for i, turn in enumerate(turns, 1):
        if turn['chapterId'] != chapter['id']:
            continue
        png = h(turn['id'] + '.png')
        speaker = h(turn['speaker'].upper())
        view = turn['visual'].get('viewId', 'オープニング・ホログラム')
        articles.append(f'''<article id="{h(turn['id'])}">
<header><span class="turn">{i:02d} / {len(turns)}</span><strong class="speaker {h(turn['speaker'])}">{speaker}</strong><span class="view">{h(view)}</span></header>
<a class="image" href="{png}" target="_blank" rel="noopener" aria-label="場面{i}の画像を原寸で開く"><img src="{png}" width="1600" height="900" loading="lazy" decoding="async" alt="場面{i}、{speaker}の画面プレビュー"></a>
<p class="dialogue">{h(turn['caption'])}</p>
<footer><a href="{png}" target="_blank" rel="noopener">画像を原寸で開く</a><a href="#{h(turn['id'])}">場面リンク</a></footer>
</article>''')
    sections.append(f'''<section id="chapter-{h(chapter['id'])}">
<div class="chapter-heading"><p class="eyebrow">CHAPTER {n:02d}</p><h2>{h(chapter['title'])}</h2><p>{h(chapter['goal'])}</p></div>
<div class="gallery">{''.join(articles)}</div><p class="back"><a href="#top">目次へ戻る ↑</a></p>
</section>''')

document = f'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{h(script['title'])} — 音声生成前の画面プレビュー</title>
<style>
:root{{color-scheme:light;--ink:#20323e;--muted:#57717d;--accent:#087f8c;--line:#d5e2e7;--paper:#fff;--bg:#edf3f6}}*{{box-sizing:border-box}}html{{scroll-behavior:smooth;scroll-padding-top:24px}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Yu Gothic",sans-serif;line-height:1.8}}a{{color:#076d7a;text-underline-offset:3px}}a:hover{{color:#164a61}}a:focus-visible{{outline:3px solid #d44836;outline-offset:4px}}main{{max-width:1480px;margin:auto;padding:28px 36px 64px}}.hero{{background:#183844;color:#f5ffff;border-radius:16px;padding:34px 38px;margin-bottom:24px}}.eyebrow{{font-size:12px;letter-spacing:.13em;font-weight:700;margin:0;color:#b6e0e2}}h1{{font-size:clamp(25px,3vw,39px);line-height:1.4;margin:6px 0 18px}}.status{{display:inline-block;background:#eaf2da;color:#314717;padding:5px 13px;border-radius:5px;font-size:16px;font-weight:700}}.hero p{{max-width:920px}}.hero .description{{color:#d4e8ed;font-size:15px;margin:16px 0 0}}nav{{padding:24px;background:var(--paper);border:1px solid var(--line);border-radius:12px;margin-bottom:40px}}nav h2{{margin:0 0 14px;font-size:20px}}.toc{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px 20px}}.toc a{{display:flex;align-items:center;justify-content:space-between;gap:10px;text-decoration:none;background:#f2f7f9;border:1px solid #e2ebef;padding:10px 12px;border-radius:6px;font-size:14px;line-height:1.6}}.toc a:hover{{background:#e1eff0}}.toc span{{white-space:nowrap;color:var(--muted);font-size:12px}}.chapter-heading{{border-left:4px solid var(--accent);padding:3px 0 3px 17px;margin-bottom:20px}}.chapter-heading .eyebrow{{color:var(--accent)}}h2{{font-size:25px;line-height:1.5;margin:3px 0 4px}}.chapter-heading>p:last-child{{font-size:14px;color:var(--muted);margin:3px 0}}.gallery{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}}article{{background:var(--paper);border:1px solid var(--line);border-radius:10px;overflow:hidden;box-shadow:0 3px 10px #193a4906;scroll-margin-top:24px}}article header{{display:flex;align-items:center;gap:13px;padding:12px 16px;border-bottom:1px solid var(--line);font-size:12px;line-height:1.4;min-height:47px}}.turn{{font-variant-numeric:tabular-nums;color:var(--muted)}}.speaker{{letter-spacing:.05em;font-size:13px}}.mako{{color:#a6377d}}.manabu{{color:#a05e14}}.ikumi{{color:#087f8c}}.view{{margin-left:auto;color:var(--muted);font-size:11px;overflow-wrap:anywhere}}.image{{display:block;background:#07141e;line-height:0}}img{{display:block;width:100%;height:auto;aspect-ratio:16/9}}.dialogue{{margin:0;padding:18px 20px;font-size:15px;line-height:1.9}}article footer{{display:flex;gap:20px;padding:0 20px 16px;font-size:12px}}.back{{text-align:right;font-size:13px;margin:15px 0 44px}}.notes{{border-top:1px solid var(--line);padding-top:22px;font-size:13px;color:var(--muted)}}.notes p{{margin:8px 0}}@media(max-width:980px){{.toc{{grid-template-columns:repeat(2,minmax(0,1fr))}}main{{padding:20px}}.gallery{{gap:18px}}.view{{display:none}}}}@media(max-width:660px){{main{{padding:12px}}.hero{{padding:24px 20px}}nav{{padding:18px}}.toc{{grid-template-columns:1fr}}.gallery{{grid-template-columns:1fr}}h2{{font-size:22px}}.dialogue{{padding:16px;font-size:15px}}}}@media(prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}}}@media print{{body{{background:white}}main{{max-width:none;padding:0}}.hero{{color:#20323e;background:white;border:1px solid #aaa}}.hero .description,.hero .eyebrow{{color:#20323e}}nav,.back{{display:none}}.gallery{{display:block}}article{{break-inside:avoid;margin-bottom:20px}}section{{break-before:page}}}}
</style></head><body><main id="top">
<div class="hero"><p class="eyebrow">EGAKUMI STUDY / LESSON 17</p><h1>{h(script['title'])}</h1><div class="status">音声生成前の画面プレビュー</div><p class="description">全{len(turns)}場面・{len(chapters)}章の画面と対話全文です。音声・動画は含まれません。完成動画の尺、音声と字幕のタイミング、連続した会話の再生確認は、音声生成と映像合成の後に確定します。画像を選ぶと原寸で確認できます。</p></div>
<nav aria-label="章別目次"><h2>章を選ぶ</h2><div class="toc">{nav}</div></nav>
{''.join(sections)}
<div class="notes"><p>画面基準：teaching-settings-v2。教材用の設定ビューであり、稼働Azure環境を取得した画面ではありません。</p><p>台詞は完成台本の表示用本文です。画像内の字幕は静止画プレビューの一部で、各場面の対話全文を画像の下へ掲載しています。</p><p>参照資料：<a href="../../../../03-subscription-management.html#key-operations">キー管理</a> ／ <a href="../../../../03-subscription-management.html#limits">利用制限</a>。<a href="../../../episodes/17/script.json">第17回の台本JSON</a></p></div>
</main></body></html>
'''
target = OUTPUT / 'index.html'
target.write_text(document)
print(json.dumps({'gallery':str(target),'turns':len(turns),'chapters':len(chapters),'audio':False,'video':False},ensure_ascii=False))
