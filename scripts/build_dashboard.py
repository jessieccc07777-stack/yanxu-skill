# -*- coding: utf-8 -*-
"""thesis-kb 工作台生成器：扫描知识库，生成 工作台.html（自包含，双击即开）。
用法: python build_dashboard.py <知识库根目录>
工作台是快照：知识库内容变化后重新运行本脚本即可刷新。"""
import argparse
import datetime
import html as html_mod
import json
import re
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
import posixpath

STAGES = ["建档", "选题", "收集资料", "开题报告", "搭框架", "写正文", "修改答辩"]
SKIP_NAMES = {"README.md", "_说明.md"}
MD_SIZE_CAP = 300 * 1024


def esc(s: str) -> str:
    return html_mod.escape(s, quote=False)


def md_inline(s: str) -> str:
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def md_to_html(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for j in range(1, min(len(lines), 60)):
            if lines[j].strip() == "---":
                lines = lines[j + 1:]
                break
    out, i, in_table, in_list = [], 0, False, None
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            if in_table:
                out.append("</table>")
                in_table = False
            if in_list:
                out.append(f"</{in_list}>")
                in_list = None
            i += 1
            continue
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(set(c) <= set("-: ") and c for c in cells):
                i += 1
                continue
            if not in_table:
                out.append("<table>")
                in_table = True
                tag = "th"
            else:
                tag = "td"
            row = "".join(f"<{tag}>{md_inline(c)}</{tag}>" for c in cells)
            out.append(f"<tr>{row}</tr>")
            i += 1
            continue
        if in_table:
            out.append("</table>")
            in_table = False
        m = re.match(r"(#{1,6})\s+(.*)", s)
        if m:
            if in_list:
                out.append(f"</{in_list}>")
                in_list = None
            level = min(len(m.group(1)) + 1, 6)
            out.append(f"<h{level}>{md_inline(m.group(2))}</h{level}>")
        elif s.startswith(">"):
            if in_list:
                out.append(f"</{in_list}>")
                in_list = None
            out.append(f"<blockquote>{md_inline(s.lstrip('> '))}</blockquote>")
        elif re.match(r"^[-*]\s+", s):
            if in_list != "ul":
                if in_list:
                    out.append(f"</{in_list}>")
                out.append("<ul>")
                in_list = "ul"
            out.append(f"<li>{md_inline(re.sub(r'^[-*]\s+', '', s))}</li>")
        elif re.match(r"^\d+[.、]\s*", s):
            if in_list != "ol":
                if in_list:
                    out.append(f"</{in_list}>")
                out.append("<ol>")
                in_list = "ol"
            out.append(f"<li>{md_inline(re.sub(r'^\d+[.、]\s*', '', s))}</li>")
        else:
            if in_list:
                out.append(f"</{in_list}>")
                in_list = None
            out.append(f"<p>{md_inline(s)}</p>")
        i += 1
    if in_table:
        out.append("</table>")
    if in_list:
        out.append(f"</{in_list}>")
    return "\n".join(out)


def count_files(d: Path):
    if not d.exists():
        return []
    return [p for p in d.rglob("*") if p.is_file()
            and p.name not in SKIP_NAMES and not p.name.startswith("_")]


def parse_progress(root: Path) -> dict:
    info = {"stage": "建档", "next_action": "", "continue_at": "",
            "deadlines": [], "last_review": None, "last_lint": None}
    p = root / "00-进度与节点.md"
    if not p.exists():
        return info
    t = p.read_text(encoding="utf-8")
    m = re.search(r"当前阶段[：:][ \t]*(.*)", t)
    if m:
        info["stage"] = m.group(1).strip()
    else:
        info["stage"] = "未知（进度表缺少「当前阶段」字段，建议升级为新格式）"
    m = re.search(r"下一步动作[：:][ \t]*(.*)", t)
    if m:
        info["next_action"] = m.group(1).strip()
    m = re.search(r"下次从哪继续[：:][ \t]*(.*)", t)
    if m:
        info["continue_at"] = m.group(1).strip()
    schedule = re.search(r"^##[^\n]*基准时间表[^\n]*\n(.*?)(?=^## |\Z)", t, re.M | re.S)
    for line in (schedule.group(1) if schedule else "").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[1]):
            continue
        name, dstr, status = cells[0], cells[1], cells[-1]
        try:
            d = datetime.date.fromisoformat(dstr)
        except ValueError:
            continue
        info["deadlines"].append({"name": name, "date": dstr,
                                  "days": (d - datetime.date.today()).days,
                                  "status": status})
    m = re.search(r"上次对账[：:]\s*(\d{4}-\d{2}-\d{2})", t)
    if m:
        info["last_review"] = m.group(1)
    m = re.search(r"上次巡检[：:]\s*(\d{4}-\d{2}-\d{2})", t)
    if m:
        info["last_lint"] = m.group(1)
    return info


def grade(content):
    match = re.search(r"^证据等级[：:][ \t]*(A(?:级|已核)?|B(?:级|待核)?|C(?:级|待核)?|缺口)(?:[ \t]*[（(][^\n]*[）)])?[ \t]*$", content, re.M)
    if not match:
        return "未评级"
    return match[1] if match[1] == "缺口" else match[1][0]


LINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]|\[([^\]]+)\]\(([^)]+)\)")


def resolve(target, owner, paths):
    target = unquote(target.split("#")[0]).strip("<> ").replace("\\", "/")
    if not target:
        return None
    local = posixpath.normpath(posixpath.join(posixpath.dirname(owner), target))
    for candidate in (local, target):
        for suffix in ("", ".md"):
            if candidate + suffix in paths:
                return candidate + suffix
    matches = [p for p in paths if target == PurePosixPath(p).stem or
               any((target + ext).endswith("/" + p) for ext in ("", ".md"))]
    return matches[0] if len(matches) == 1 else None


def sections(root, paths):
    outlines = list((root / "04-提纲").glob("提纲-v*.md"))
    if not outlines:
        return []
    outline = max(outlines, key=lambda p: ([int(x) for x in re.findall(r"\d+", p.stem)], p.name))
    owner = outline.relative_to(root).as_posix()
    result = []
    for m in re.finditer(r"^(#{3,6}) ([^\n]+)\n(.*?)(?=^#{1,6} |\Z)", outline.read_text(encoding="utf-8"), re.M | re.S):
        body = m[3]
        mount = re.search(r"已挂载[：:]([^\n]*(?:\n[ \t]*[-*]?[ \t]*(?:\[\[|\[[^\]]+\]\()[^\n]*)*)", body)
        refs, broken = [], []
        for link in LINK.finditer(mount[1] if mount else ""):
            target = link[1] or link[4]
            path = resolve(target, owner, paths)
            if path and "模板" not in PurePosixPath(path).name:
                refs.append(path)
            else:
                broken.append(target)
        result.append(dict(title=m[2].strip(), level=len(m[1]), body=body.strip(), refs=list(dict.fromkeys(refs)), broken=broken))
    return result


def completed(status):
    return status.strip() in {"完成", "已完成", "已提交", "✅"} or status.strip().startswith("✅")


def latest_mtime(root: Path, dirs) -> float:
    latest = 0.0
    for d in dirs:
        for p in count_files(root / d):
            latest = max(latest, p.stat().st_mtime)
    return latest


def build_reminders(root: Path, prog: dict, inbox_n: int) -> list:
    today = datetime.date.today()
    rems = []
    for dl in prog["deadlines"]:
        if completed(dl["status"]):
            continue
        if dl["days"] < 0:
            rems.append(("high", f"「{dl['name']}」已逾期 {-dl['days']} 天", "核对交付状态或调整计划"))
        elif dl["days"] <= 3:
            rems.append(("high", f"距「{dl['name']}」只剩 {dl['days']} 天", "把它拆成今天能做的最小一步"))
        elif dl["days"] <= 7:
            rems.append(("mid", f"距「{dl['name']}」还有 {dl['days']} 天", "核对当前进度是否撑得住"))
        elif dl["days"] <= 14:
            rems.append(("low", f"「{dl['name']}」进入 14 天倒计时", "排进本周计划"))
    for label, key in (("进度对账", "last_review"), ("巡检", "last_lint")):
        v = prog[key]
        overdue = True
        if v:
            try:
                overdue = (today - datetime.date.fromisoformat(v)).days > 7
            except ValueError:
                overdue = True
        if not v:
            rems.append(("low", f"尚无{label}记录", f"完成首次{label}后登记日期"))
        elif overdue:
            rems.append(("mid", f"{label}已超过 7 天未跑", f"对 AI 说「跑一次{label}」"))
    if inbox_n > 5:
        rems.append(("mid", f"收件箱积压 {inbox_n} 份未分拣资料", "花 15 分钟做入库分拣"))
    last_work = latest_mtime(root, ["02-选题与开题", "04-提纲", "05-正文"])
    if last_work:
        idle = (datetime.datetime.now() - datetime.datetime.fromtimestamp(last_work)).days
        if idle > 7:
            rems.append(("low", f"距上次实质推进已 {idle} 天", "挑一个 10 分钟能完成的最小动作重启"))
    try:
        kaiti = [p for p in (root / "02-选题与开题").glob("开题报告*.md") if "模板" not in p.name]
        tigang = list((root / "04-提纲").glob("提纲*.md"))
        if kaiti and tigang and \
                max(p.stat().st_mtime for p in kaiti) > max(p.stat().st_mtime for p in tigang):
            rems.append(("low", "开题报告有更新，提纲可能未按定稿校准",
                         "对照开题报告的论文结构校准 04-提纲 骨架"))
    except OSError:
        pass
    order = {"high": 0, "mid": 1, "low": 2}
    rems.sort(key=lambda r: order[r[0]])
    return rems


def stage_index(stage: str) -> int:
    for i, name in enumerate(STAGES):
        if stage.strip().startswith(name):
            return i
    for i, s in enumerate(STAGES):
        if s in stage:
            return i
    if "开题" in stage:
        return 3
    if "提纲" in stage or "框架" in stage:
        return 4
    if "正文" in stage or "写作" in stage:
        return 5
    if "答辩" in stage or "修改" in stage:
        return 6
    if "资料" in stage or "素材" in stage or "收集" in stage:
        return 2
    if "选题" in stage:
        return 1
    return 0


GUIDE = [['明确写作条件',
  '写作约束清单',
  '第一次使用，从你自己的情况开始。准备好学校要求、导师方向和大致可投入的时间。',
  '回答建档问题，确认哪些信息可以披露。',
  '把回答整理为画像、导师方向和学校节点。',
  '带着这份清单，盘点适合研究的问题。',
  '请带我开始建档，一次问一项。先问我的研究兴趣、可以接触的资料和时间约束。'],
 ['选定研究问题',
  '题目与选择理由',
  '接着上一步的背景和约束，看看哪些问题有研究价值，也有条件取得资料。',
  '选一个愿意研究的问题，与导师沟通。',
  '盘点候选题、资料基础和缺口，记录选择理由。',
  '用选定的问题决定接下来收什么材料。',
  '请根据我的画像、导师方向和已有资料盘点候选题，列出各自的资料缺口，留给我选择。'],
 ['收集并核对资料',
  '证据卡片与缺口',
  '带着研究问题找原文。每读一份材料，先问它能回答哪一个问题。',
  '回原文核对事实，确认材料能支持到什么程度。',
  '提取要点、标来源位置和核验等级，列出待补项。',
  '把有依据的研究思路整理成开题报告。',
  '请从当前研究问题反推需要的资料，选一份材料起草卡片，给我原文位置核查。'],
 ['完成开题报告',
  '研究方案与时间表',
  '把已选的问题、方法与现有证据整理给导师审阅。',
  '审定研究边界、方法和可执行的写作计划。',
  '按学校要求起草，记录反馈；定稿后提取时间表。',
  '依照定稿承诺的内容，安排每一章要回答什么。',
  '请结合学校要求、导师反馈和现有证据，列出开题报告需要确认的研究方案。'],
 ['把问题排进章节',
  '带证据的论文提纲',
  '开题定下研究问题后，把它拆成章和节。每节写清一个问题，并把能用的材料放到它下面。',
  '看这一节的问题、原文和缺口，决定先写、补证还是收窄判断。',
  '按开题校准提纲，把卡片连到小节，列出空白和待核之处。',
  '带着有依据的小节进入正文。全篇材料挂载达到一半时，逐节检查起草条件。',
  '请打开当前提纲，列出本节要回答的问题、已有来源和仍不能证明的判断，让我决定是否起草。'],
 ['逐节写出正文',
  '章节草稿',
  '从提纲中选一个证据准备好的小节，把核过的材料组织成论证。',
  '定立论、取舍和语气，核对关键判断是否超出证据。',
  '按小节起草，检查前后联系、数字和引用口径。',
  '把草稿送审，带着导师反馈进入修改。',
  '请从当前提纲选定的小节开始，先列出关键判断与出处，等我确认后起草。'],
 ['修改与准备答辩',
  '定稿与答辩材料',
  '从章节草稿和导师原话出发，逐项落实意见，准备解释关键取舍。',
  '决定怎么改，确认定稿，练习回答证据薄弱处。',
  '登记意见、追踪落实，按证据缺口整理可能的提问。',
  '确认修改完成并整理定稿、答辩材料和修改记录。',
  '请对照导师反馈和最新正文列出未落实项，再从证据薄弱处整理答辩问题。']]

STYLE = """
:root{--ink:#243832;--pine:#195a4b;--paper:#f6f5ef;--line:#d9dfd5;--muted:#566760;--white:#fffefa}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.65 "Microsoft YaHei","PingFang SC",sans-serif}
header,main,footer{max-width:1080px;margin:auto;padding:0 clamp(16px,4vw,46px)}header{display:flex;align-items:center;justify-content:space-between;padding-top:22px;padding-bottom:16px;border-bottom:1px solid var(--line)}
header b{letter-spacing:.08em}button,select,input{font:inherit}a,button{color:var(--pine)}button,.action{border:1px solid var(--line);border-radius:7px;background:var(--white);padding:7px 14px;cursor:pointer;text-decoration:none}button:hover,.action:hover{background:#e8f0e9}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #c38331;outline-offset:3px}
nav{display:flex;gap:8px;margin:22px 0;flex-wrap:wrap}nav button{border-color:transparent;background:transparent}nav .on{background:var(--pine);color:white}.panel,.hero,details.sheet{background:var(--white);border:1px solid var(--line);border-radius:14px;padding:clamp(16px,3vw,28px);margin:14px 0}
.hero{background:#204d42;color:#f8f7ee;border:0;position:relative;overflow:hidden;padding:30px clamp(20px,4vw,44px)}.hero:after{content:"";position:absolute;pointer-events:none;right:-66px;top:-135px;width:300px;height:300px;border:1px solid #ffffff22;border-radius:50%;box-shadow:0 0 0 24px #ffffff08,0 0 0 50px #ffffff08,0 0 0 78px #ffffff08}.hero>*{position:relative;z-index:1}.hero small{color:#c4ddcf}.hero button{margin-top:7px}.hero h1{font:600 clamp(25px,4vw,39px)/1.35 "Songti SC",SimSun,serif;margin:10px 0;max-width:850px}.hero p{color:#e0ebe3}
h2{font-size:22px;margin:8px 0 14px}h3{margin:14px 0 6px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid .panel{margin:0}.stat{font:600 32px Georgia,serif;color:var(--pine)}.muted,small,footer{color:var(--muted)}.bar{height:6px;background:#e4e9df;border-radius:4px;margin:14px 0}.bar i{display:block;background:#559279;height:100%;border-radius:4px}
.row{padding:11px 0;border-bottom:1px solid var(--line)}.tag{display:inline-block;color:#305e50;background:#e9f0e8;border-radius:6px;padding:1px 8px;font-size:13px;margin-right:6px}.warn{color:#824d1c;background:#f6ead8}details.sheet summary{cursor:pointer;font-weight:600}details.sheet[open] summary{margin-bottom:12px}.tools{display:flex;gap:8px;flex-wrap:wrap}.tools input,.tools select,textarea{padding:9px;border:1px solid var(--line);border-radius:6px;background:white}.tools input{flex:1;min-width:150px}.library{display:grid;grid-template-columns:minmax(185px,32%) 1fr;gap:15px}.list{max-height:60vh;overflow:auto}.doclink{display:block;text-align:left;border:none;background:none;text-decoration:underline;text-underline-offset:3px;overflow-wrap:anywhere}.list button{width:100%;text-decoration:none;border-bottom:1px solid var(--line);border-radius:0}.reader{min-width:0;overflow-wrap:anywhere;overflow:auto}.reader a{cursor:pointer;text-decoration:underline}table{border-collapse:collapse;display:block;overflow:auto}td,th{border-bottom:1px solid var(--line);padding:8px;text-align:left}blockquote{border-left:3px solid #7ba18a;margin:10px 0;padding-left:12px}pre{white-space:pre-wrap;font:inherit}footer{font-size:13px;margin:24px auto}textarea{width:100%;height:140px}[hidden]{display:none!important}@media(max-width:700px){.library,.grid{grid-template-columns:1fr}.list{max-height:220px}.hero:after{opacity:.5}nav{gap:2px}nav button{padding:7px 10px}}
"""

STYLE += """
.road{margin:22px 0 14px}.road h1{font:600 clamp(24px,3vw,34px)/1.4 SimSun,serif;margin:0}.road p{margin:5px 0 16px}.steps{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:5px}.steps button{position:relative;text-align:left;padding:10px 8px;line-height:1.45;display:grid;align-content:start;gap:6px}.steps small{font-size:12px}.steps strong{font-size:15px}.steps span{font-size:12px;color:var(--muted)}.steps [aria-pressed=true]{border-color:var(--pine);box-shadow:inset 0 3px var(--pine);background:#e7efe8}.steps [aria-current=step] small{color:var(--pine);font-weight:700}
.guide h2{margin:2px 0}.guide small{color:var(--pine)}.roles{display:grid;grid-template-columns:1fr 1fr;gap:14px}.roles p{margin:4px 0}.roles>div{padding:10px 14px;background:#f2f4ee;border-radius:8px}.handoff{border-top:1px solid var(--line);padding-top:12px}.guide .actionMain{background:var(--pine);color:white}.quote{margin:12px 0;padding-left:12px;border-left:3px solid #8da997}.guide .tools{align-items:center}.guide p{margin:8px 0 12px}@media(max-width:760px){.steps{grid-template-columns:repeat(4,minmax(0,1fr))}.steps span{display:none}.roles{grid-template-columns:1fr}.road{margin-top:12px}}
"""

PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>论文工作台</title><style>__STYLE__</style>
<header><b>▤　论文工作台</b><button class="action" id="openManual">使用说明</button></header><main>
<nav aria-label="工作区"><button data-tab="home">工作概览</button><button data-tab="outline">提纲与证据</button><button data-tab="library">全库检索</button></nav>
<section id="home"><div class="road"><h1>从选题到答辩，论文怎么推进？</h1><p>先确定要回答的问题，再找资料、核原文、写成段落。写不下去时，回到缺证据的那一步。</p><div id="steps" class="steps" aria-label="论文七步流程"></div></div>
<article class="panel guide"><small id="stage"></small><h2 id="guideTitle"></h2><p id="origin"></p><div class="roles"><div><b>你来做</b><p id="person"></p></div><div><b>AI 来做</b><p id="agent"></p></div></div><p class="handoff" id="handoff"></p><div class="quote" id="record"></div><div class="tools"><button class="actionMain" id="continue"></button><button id="prompt">复制给 AI 的话</button><button id="returnStage" hidden>回到当前步骤</button></div><p id="samplePrompt" class="muted"></p><div id="guideCopy" role="status"></div></article>
<div class="grid"><article class="panel"><small>提纲挂载覆盖率</small><div class="stat" id="pct"></div><div class="bar"><i id="fill"></i></div><span id="count"></span></article><article class="panel"><small>最近待交付</small><h2 id="due"></h2><span id="date"></span></article></div>
<div class="panel"><h2>待办提醒</h2><div id="alerts"></div></div><details class="sheet"><summary>时间表与项目概况</summary><div id="timeline"></div><p id="stats"></p></details></section>
<section id="outline" hidden><h2>提纲与证据</h2><div id="sections"></div></section>
<section id="library" hidden><h2>全库检索</h2><div class="tools"><input id="search" aria-label="搜索标题或正文" placeholder="搜索文件名或已收录的正文"><select id="filter" aria-label="文件范围"><option>全部文件</option><option>当前材料</option><option>待核卡片</option><option>归档旧稿</option></select></div><div class="library"><div id="results" class="list panel"></div><article class="reader panel"><button id="back" hidden>返回上一处</button><div id="reader">选择文件查看内容。</div></article></div></section>
<div id="copyState" role="status"></div><textarea id="manualCopy" aria-label="手动复制任务" hidden></textarea></main><footer id="stamp"></footer>
<script type="application/json" id="payload">__DATA__</script><script>__SCRIPT__</script></html>"""

JS = """
const d=JSON.parse(document.querySelector('#payload').textContent);
const el=id=>document.getElementById(id);
function node(tag,text,cls){const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n}
function button(text,fn,cls){const b=node('button',text,cls);b.onclick=fn;return b}
function tab(name){for(const n of ['home','outline','library'])el(n).hidden=n!==name;
 document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('on',b.dataset.tab===name);b.setAttribute('aria-pressed',String(b.dataset.tab===name))});
 el('copyState').textContent='';el('manualCopy').hidden=true}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>tab(b.dataset.tab));
const paths=Object.keys(d.files), stack=[];let current=null;
function show(path,remember=true){const f=d.files[path];if(!f)return;
 if(remember){const previous=current&&!el('library').hidden?current:el('outline').hidden?'@home':'@outline';if(previous!==path)stack.push(previous)}
 tab('library');current=path;const r=el('reader');r.replaceChildren(node('small',path));
 if(f.html!==null){const article=document.createElement('article');article.innerHTML=f.html;r.append(article)}
 else{const a=node('a','打开原文件');a.href=path.split('/').map(encodeURIComponent).join('/');a.target='_blank';r.append(node('p','附件可用本机支持的软件打开。'),a)}
 el('back').hidden=!stack.length;r.scrollIntoView({block:'nearest'})}
const manualButton=el('openManual');
manualButton.hidden=!d.files['使用说明.md'];
manualButton.onclick=()=>show('使用说明.md');
el('reader').onclick=e=>{const a=e.target.closest('[data-file]');if(a){e.preventDefault();show(a.dataset.file)}};
el('back').onclick=()=>{const p=stack.pop();if(p.startsWith('@')){current=null;tab(p.slice(1))}else show(p,false)};
function search(){const q=el('search').value.trim().toLowerCase(), filter=el('filter').value;const list=el('results');list.replaceChildren();
 for(const path of paths){const f=d.files[path];if(filter==='归档旧稿'&&!path.startsWith('07-归档/'))continue;
 if(filter==='当前材料'&&(!path.startsWith('03-素材/')||f.template))continue;
 if(filter==='待核卡片'&&(!f.grade||!['B','C','未评级'].includes(f.grade)))continue;
 if(!(path+' '+f.text).toLowerCase().includes(q))continue;list.append(button(path.split('/').pop(),()=>show(path),'doclink'))}
 if(!list.children.length)list.append(node('p','没有符合条件的文件。'))}
el('search').oninput=search;el('filter').onchange=search;
async function copy(text){try{await navigator.clipboard.writeText(text);el('copyState').textContent='本节任务已复制，可粘贴给 AI。'}catch{const t=el('manualCopy');t.hidden=false;t.value=text;t.focus();t.select();el('copyState').textContent='请复制选中的任务文字。'}}
const panels=[];
for(const s of d.sections){const box=document.createElement('details');box.className='sheet';panels.push(box);
 box.append(node('summary',s.title+'　·　'+(s.refs.length?s.refs.length+' 份材料':s.has_children?'展开下方子节查看材料':'待补材料')));
 box.append(node('pre',s.summary||'本节尚未填写问题与判断。'));
 for(const p of s.refs){box.append(button((d.files[p].grade?d.files[p].grade+'　':'')+p.split('/').pop(),()=>show(p),'doclink'))}
 if(s.broken.length)box.append(node('p','待定位引用　'+s.broken.join('、'),'warn'));
 box.append(button('复制本节任务',()=>copy(s.task)));el('sections').append(box)}
if(!panels.length)el('sections').append(node('p','尚无提纲小节，可先从当前阶段的任务开始。','panel'));
const p=d.progress;
const steps=d.guide;
const names=['看我的建档资料','查看选题盘点','查找论文材料','查看开题材料','查看本节与证据','查看章节草稿','查看修改记录'];
function openStage(i){
 if(i===4 && panels.length){tab('outline');const box=panels[d.focus>=0?d.focus:0];box.open=true;box.scrollIntoView({block:'center'});return}
 const folders=['01-要求与规范/使用者画像.md','02-选题与开题/选题池.md','03-素材/','02-选题与开题/开题报告','', '05-正文/','06-导师反馈/'];
 if(i<=1 && d.files[folders[i]]){show(folders[i]);return}
 tab('library');el('filter').value='全部文件';el('search').value=folders[i];search()
}
function selectStage(i){
 const g=steps[i], here=i===d.stageIndex;
 [...el('steps').children].forEach((b,j)=>b.setAttribute('aria-pressed',String(i===j)));
 el('stage').textContent=(here?'你在这里':'流程预览')+'　·　第 '+(i+1)+' 步，共 7 步';
 el('guideTitle').textContent=g[0];el('origin').textContent=g[2];el('person').textContent=g[3];el('agent').textContent=g[4];
 el('handoff').textContent='本步产出：'+g[1]+'。'+g[5];
 const record=el('record');record.replaceChildren();record.hidden=!here||!(p.next_action||p.continue_at);
 if(here&&p.next_action)record.append(node('strong','眼下要做：'+p.next_action));
 if(here&&p.continue_at)record.append(node('div','上次停在：'+p.continue_at));
 const prompt=(here&&i===4&&d.focus>=0)?g[6]+'\\n\\n'+d.sections[d.focus].task:g[6];
 el('samplePrompt').textContent='到 AI 对话里可以这样开口：“'+g[6]+'”';
 el('guideCopy').textContent='';el('manualCopy').hidden=true;
 el('prompt').onclick=async()=>{await copy(prompt);el('guideCopy').textContent=el('manualCopy').hidden?'已复制，请粘贴到 AI 对话。':'请复制页面下方已选中的任务。'};
 const action=el('continue');action.textContent=names[i];action.onclick=()=>openStage(i);el('prompt').classList.toggle('actionMain',i===0);action.classList.toggle('actionMain',i!==0);el('prompt').textContent=i===0?'复制建档问题，去 AI 开始':'复制给 AI 的话';
 el('returnStage').hidden=here;el('returnStage').onclick=()=>selectStage(d.stageIndex)
}
steps.forEach((g,i)=>{const b=button('',()=>selectStage(i));b.append(node('small',i===d.stageIndex?'第 '+(i+1)+' 步 · 当前':'第 '+(i+1)+' 步'),node('strong',g[0]),node('span',g[1]));if(i===d.stageIndex)b.setAttribute('aria-current','step');el('steps').append(b)});
selectStage(d.stageIndex);
el('pct').textContent=d.coverage+'%';el('fill').style.width=d.coverage+'%';el('count').textContent=d.covered+'／'+d.total+' 个小节已关联材料';
el('due').textContent=d.due?.name||'暂无待交付节点';el('date').textContent=d.due?d.due.date+'　·　'+(d.due.days<0?'逾期 '+(-d.due.days)+' 天':'还有 '+d.due.days+' 天'):'可在基准时间表中补充日期';
for(const [level,msg,action] of d.alerts){const row=node('div','','row');row.append(node('span',level==='high'?'优先':'提示',level==='high'?'tag warn':'tag'),node('strong',msg),node('div',action,'muted'));el('alerts').append(row)}
if(!d.alerts.length)el('alerts').textContent='当前没有触发提醒。';
for(const item of p.deadlines)el('timeline').append(node('div',item.date+'　'+item.name+'　'+item.status,'row'));
el('stats').textContent=d.stats;el('stamp').textContent='快照生成于 '+d.stamp+'　·　AI 更新知识库后重新生成';search();tab('home');
"""

def render_document(text, owner, paths):
    links = []
    def substitute(match):
        target = match[1] or match[4]
        label = match[2] or match[3] or target
        path = resolve(target, owner, paths)
        if path:
            link = f'<a href="#library" data-file="{html_mod.escape(path, quote=True)}">{esc(label)}</a>'
        elif target.startswith(("https://", "http://")):
            link = f'<a href="{html_mod.escape(target, quote=True)}" target="_blank" rel="noopener">{esc(label)}</a>'
        else:
            link = esc(label) + "（待定位）"
        links.append(link)
        return f"⟪引用{len(links)-1}⟫"
    # Render library content as text before inserting our own anchor markup.
    prefix = "⟪引用"
    while prefix in text:
        prefix += "索"
    def token(match):
        return substitute(match).replace("⟪引用", prefix)
    result = md_to_html(LINK.sub(token, text))
    return re.sub(re.escape(prefix) + r"(\d+)⟫", lambda m: links[int(m[1])], result)


def write_page(path, content):
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        stream.write(content)
    Path(stream.name).replace(path)


def generate(root):
    progress = parse_progress(root)
    paths = {p.relative_to(root).as_posix() for p in root.rglob("*")
             if p.is_file() and p.name not in {"工作台.html","使用说明.html"}
             and (p.relative_to(root).parts[0][:3] in {f"{i:02d}-" for i in range(1,8)} or (p.parent == root and (p.name in {"00-进度与节点.md","使用说明.md","README.md"} or p.match("巡检报告-*.md"))))
             and not any(part.startswith(".") or part == "工作台审阅版" for part in p.relative_to(root).parts)}
    docs = {}
    grades = {}
    for path in sorted(paths):
        file = root / path
        content = None
        if file.suffix.lower() in (".md", ".txt") and file.stat().st_size <= MD_SIZE_CAP:
            content = file.read_text(encoding="utf-8-sig", errors="replace")
        template = "模板" in file.name or file.name.startswith("_") or file.name == "README.md"
        rating = None
        if path.startswith("03-素材/04-摘录卡片/") and not template and content:
            rating = grade(content)
            grades[rating] = grades.get(rating, 0) + 1
        display = None
        if content is not None:
            display = render_document(content, path, paths)
            if rating:
                meta = re.search(r"^来源[：:][ \t]*(.+)$", content, re.M)
                if meta:
                    source = meta[1].strip()
                    display += render_document("来源记录："+source, path, paths)
                    matches = []
                    for candidate in sorted(paths):
                        name = PurePosixPath(candidate)
                        if candidate == path or not name.suffix or name.stem in {"README", "_说明"}:
                            continue
                        ref_id = re.match(r"^(R\d+)(?=[- _])", name.name)
                        cited = name.name in source or (name.suffix == ".md" and name.stem in source)
                        if ref_id and re.search(r"\b"+ref_id[1]+r"\b",source):
                            cited = True
                        if cited and sum(PurePosixPath(other).name == name.name for other in paths) == 1:
                            matches.append(candidate)
                    if matches:
                        display += render_document("库内文件："+ "、".join(f"[[{p}|{PurePosixPath(p).name}]]" for p in matches),path,paths)
                    elif not LINK.search(source):
                        display += "<p>来源已记录，尚无可定位的库内文件。</p>"
        docs[path] = dict(html=display, text=content or "", grade=rating, template=template)
    records = sections(root,paths)
    for sec in records:
        summary = re.sub(r"(?m)^[-* ]*已挂载[：:].*$", "", sec["body"]).strip()
        sec["summary"] = summary
        evidence = []
        for path in sec["refs"]:
            card = docs[path]
            details = card["text"][:1600] if card["grade"] else ""
            evidence.append(path + "\n" + details)
        sec["task"] = "\n".join(["请核对本节可支持的判断，指出需要补证之处。",sec["title"],summary,
                                  *evidence, "待定位引用："+"、".join(sec["broken"])])
    units = []
    for section in records:
        if section["level"] == 3:
            units.append(False)
        if section["level"] == 3:
            parent = section
            parent["has_children"] = False
        elif units:
            parent["has_children"] = True
        if units and section["refs"]:
            units[-1] = True
    covered = sum(units)
    active = sorted((dl for dl in progress["deadlines"] if not completed(dl["status"])),key=lambda dl:dl["days"])
    alerts = build_reminders(root, progress, len(count_files(root/"03-素材"/"00-收件箱")))
    focus = -1
    if any(word in progress["stage"] for word in ("搭框架","提纲","写正文","答辩","修改")) and records:
        titles = {s["title"].split()[0]: i for i,s in enumerate(records)}
        mentions = re.findall(r"(?<![\d.])\d+(?:\.\d+)+(?![\d.])", progress["next_action"]+" "+progress["continue_at"])
        focus = next((titles[key] for key in mentions if key in titles), -1)
    start = "00-进度与节点.md"
    if stage_index(progress["stage"]) == 0 and "01-要求与规范/使用者画像.md" in docs:
        start = "01-要求与规范/使用者画像.md"
    elif stage_index(progress["stage"]) == 1 and "02-选题与开题/选题池.md" in docs:
        start = "02-选题与开题/选题池.md"
    elif stage_index(progress["stage"]) == 3:
        proposals = [p for p in paths if p.startswith("02-选题与开题/开题报告") and p.endswith(".md") and "模板" not in p]
        if proposals:
            start = max(proposals,key=lambda p:(root/p).stat().st_mtime)
    if start not in paths:
        start = next(iter(docs),"")
    counts = "　".join(f"{g} {grades.get(g,0)}" for g in ("A","B","C","缺口","未评级"))
    def file_count(prefix):
        return sum(p.startswith(prefix) and not doc["template"] for p,doc in docs.items())
    stats = (f"一手资料 {file_count('03-素材/01-')}　 二手资料 {file_count('03-素材/02-')}　"
             f" 正文文件 {file_count('05-正文/')}　 导师记录 {file_count('06-导师反馈/')}　 卡片等级　{counts}")
    data = dict(files=docs,sections=records,progress=progress,covered=covered,focus=focus,start=start,
                coverage=round(100*covered/len(units)) if units else 0,total=len(units),due=active[0] if active else None,
                stats=stats,stageIndex=stage_index(progress["stage"]),guide=GUIDE,alerts=alerts,stamp=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    page = PAGE.replace("__STYLE__",STYLE).replace("__SCRIPT__",JS).replace("__DATA__",json.dumps(data,ensure_ascii=False).replace("<","\\u003c"))
    write_page(root/"工作台.html",page)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root",type=Path)
    args = parser.parse_args()
    if not args.root.is_dir():
        parser.error("知识库目录不存在")
    generate(args.root)
    print("已生成工作台（内含使用说明）")

