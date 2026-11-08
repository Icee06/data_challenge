"""Build the CQA DEV dataset workbook from the organizers' API response."""
import json, sys
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.comments import Comment

SRC, OUT = sys.argv[1], sys.argv[2]
WORK = "p1-ru-001 p1-ru-003 p1-ru-007 p1-ru-010 p1-ru-020 p1-ru-025 p1-kz-001 p1-kz-004 p1-en-003 p1-en-007 p4-ru-001 p4-ru-002".split()
HELD = "p1-ru-006 p1-ru-015 p1-ru-024 p1-kz-003 p1-kz-010 p1-en-006 p1-en-010 p4-ru-005".split()
SAMPLE = {**{i: "Working" for i in WORK}, **{i: "Held-out" for i in HELD}}
raw = json.loads(open(SRC, "rb").read().decode("utf-8-sig"))
recs = raw["data"]["records"]
group = raw["data"].get("dataset_group_id", "DEV")

FONT = "Arial"
F = lambda **k: Font(name=FONT, size=k.pop("size", 10), **k)
HEAD_FILL = PatternFill("solid", fgColor="0E2419")
HEAD_FONT = F(bold=True, color="FFFFFF")
INPUT_FILL = PatternFill("solid", fgColor="FFF2B3")   # cells to fill in
TITLE = F(bold=True, size=14)
WRAP = Alignment(wrap_text=True, vertical="top")
TOP = Alignment(vertical="top")
thin = Side(style="thin", color="D8DDE4")

wb = Workbook()

def header(ws, row, cols, widths=None):
    for i, name in enumerate(cols, 1):
        c = ws.cell(row=row, column=i, value=name)
        c.font = HEAD_FONT; c.fill = HEAD_FILL; c.alignment = Alignment(vertical="center", wrap_text=True)
    if widths:
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[row].height = 30

def add_table(ws, name, ref, style="TableStyleLight1"):
    t = Table(displayName=name, ref=ref)
    t.tableStyleInfo = TableStyleInfo(name=style, showRowStripes=True)
    ws.add_table(t)

def to_sec(ts):
    ts = str(ts or "")
    if len(ts) <= 5 and ":" in ts:
        m, s = ts.split(":"); return int(m) * 60 + int(s)
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None

ROLE = {"client": "Client", "operator": "Operator", "bot": "Bot", "system": "System"}

# ---------------- Checklist (from the checklist PDF) ----------------
CHECKLIST = [
    ("c1", "Установление контакта", "Начало и понимание запроса", "yesno", 10, "No", "", "", "Да — оператор поздоровался на языке клиента и представился в первом ответе."),
    ("c2", "Выявление потребности", "Начало и понимание запроса", "yesno", 15, "Yes", "Данных для ответа хватало с самого начала", "", "Да — оператор учёл известные детали и уточнил недостающее до ответа."),
    ("c3", "Идентификация", "Безопасность и решение", "yesno", 20, "Yes", "Запрос не требовал идентификации", "", "Да — проверка личности подтверждена до раскрытия данных или защищённого действия. Если проверка требовалась и не подтверждена в чате — Нет."),
    ("c4", "Корректная и полная консультация", "Безопасность и решение", "scale", 20, "Yes", "Чат закончился до того, как клиент сформулировал запрос", "20, 15, 10, 5, 0",
     "20 — все существенные вопросы закрыты или дан корректный следующий шаг. 15 — основной вопрос решён, пропущена второстепенная деталь. 10 — часть запроса закрыта, существенный вопрос открыт. 5 — общая подсказка, клиенту непонятно, как действовать. 0 — ответа по существу нет или рекомендация ошибочна."),
    ("c5", "Эмпатия и клиентоориентированность", "Ведение и завершение", "yesno", 15, "No", "", "", "Да — оператор вежлив и, если клиент выразил недовольство или описал неудобство, отреагировал на конкретную проблему."),
    ("c6", "Управление диалогом, скорость и грамотность", "Ведение и завершение", "scale", 15, "No", "", "10, 7, 4, 0",
     "10 — ответы ясные и связные, при ожидании оператор предупредил. 7 — один заметный сбой. 4 — клиенту приходится переспрашивать. 0 — ход диалога непонятен."),
    ("c7", "Завершение", "Ведение и завершение", "yesno", 5, "Yes", "Чат оборвался до возможности его завершить", "", "Да — оператор подвёл итог или предложил дальнейшую помощь и попрощался."),
    ("c8", "Грубые ошибки", "Грубые нарушения", "flag", 0, "No", "", "",
     "Да — оскорбление клиента, предложение платной услуги в обход компании или раскрытие конфиденциальной информации. Без баллов, отмечается отдельно."),
]
VALID = {"yesno": ["YES", "NO"], "flag": ["YES", "NO"]}

# ---------------- Messages ----------------
wsM = wb.active; wsM.title = "Messages"
mcols = ["Key", "Conversation ID", "Message #", "Timestamp (as given)", "Seconds from start", "Speaker", "Text", "Characters"]
header(wsM, 1, mcols, [14, 15, 10, 26, 11, 11, 90, 11])
r = 2
for rec in recs:
    t = rec["transcript"]
    t0 = to_sec(t[0]["timestamp"]) if t else None
    for i, m in enumerate(t):
        s = to_sec(m["timestamp"])
        wsM.cell(r, 1, f'=B{r}&"#"&C{r}')
        wsM.cell(r, 2, rec["conversation_id"])
        wsM.cell(r, 3, i)
        wsM.cell(r, 4, str(m["timestamp"]))
        wsM.cell(r, 5, round(s - t0, 1) if s is not None and t0 is not None else None)
        wsM.cell(r, 6, ROLE.get(m["speaker"], m["speaker"]))
        wsM.cell(r, 7, m["text"])
        wsM.cell(r, 8, f"=LEN(G{r})")
        r += 1
M_LAST = r - 1
for row in wsM.iter_rows(min_row=2, max_row=M_LAST):
    for c in row:
        c.font = F(); c.alignment = WRAP if c.column == 7 else TOP
wsM.freeze_panes = "C2"
add_table(wsM, "Messages", f"A1:H{M_LAST}")
wsM["A1"].comment = Comment("Conversation ID and message number joined, used by the Manual scoring sheet to look up evidence timestamps.", "CQA")
wsM["C1"].comment = Comment("Counts from 0, the same numbering the app shows (#0, #1 …) and the AI uses for evidence_index.", "CQA")
wsM["E1"].comment = Comment("Chats: MM:SS converted to seconds. Calls: ISO 8601 time minus the first message's time. Calculated from the dataset when this file was built.", "CQA")

MR = lambda col: f"Messages!${col}$2:${col}${M_LAST}"

# ---------------- Conversations ----------------
wsC = wb.create_sheet("Conversations", 0)
ccols = ["Conversation ID", "Language", "Channel", "Type", "Messages", "Client", "Operator", "Bot", "System",
         "Duration (s)", "First timestamp", "Last timestamp", "Client's first message", "Audio URL", "Manual score", "Criteria scored", "Gross violation"]
header(wsC, 1, ccols, [15, 10, 12, 8, 10, 8, 10, 7, 8, 11, 24, 24, 60, 40, 11, 10, 11])
SC_LAST = 1 + len(recs) * len(CHECKLIST)
SR = lambda col: f"'Manual scoring'!${col}$2:${col}${SC_LAST}"
for k, rec in enumerate(recs):
    r = k + 2; t = rec["transcript"]
    first_client = next((m["text"] for m in t if m["speaker"] == "client" and m["text"].strip()), "")
    wsC.cell(r, 1, rec["conversation_id"])
    wsC.cell(r, 2, rec["language"])
    wsC.cell(r, 3, rec["channel"])
    wsC.cell(r, 4, "Call" if rec["channel"] == "voice call" else "Chat")
    wsC.cell(r, 5, f'=COUNTIF({MR("B")},A{r})')
    for col, sp in zip("FGHI", ["Client", "Operator", "Bot", "System"]):
        wsC[f"{col}{r}"] = f'=COUNTIFS({MR("B")},$A{r},{MR("F")},"{sp}")'
    wsC.cell(r, 10, f'=_xlfn.MAXIFS({MR("E")},{MR("B")},A{r})')
    wsC.cell(r, 11, str(t[0]["timestamp"]) if t else "")
    wsC.cell(r, 12, str(t[-1]["timestamp"]) if t else "")
    wsC.cell(r, 13, first_client)
    wsC.cell(r, 14, rec.get("audio_url") or "")
    wsC.cell(r, 16, f'=COUNTIFS({SR("A")},A{r},{SR("D")},"<>")')
    wsC.cell(r, 15, f'=IF(P{r}<{len(CHECKLIST)},"",IF(SUMIFS({SR("L")},{SR("A")},A{r})=0,"n/a",ROUND(SUMIFS({SR("N")},{SR("A")},A{r}),2)))')
    wsC.cell(r, 17, f'=IF(COUNTIFS({SR("A")},A{r},{SR("B")},"c8",{SR("D")},"YES")>0,"Yes",IF(COUNTIFS({SR("A")},A{r},{SR("B")},"c8",{SR("D")},"NO")>0,"No",""))')
C_LAST = len(recs) + 1
for row in wsC.iter_rows(min_row=2, max_row=C_LAST):
    for c in row:
        c.font = F(); c.alignment = WRAP if c.column == 13 else TOP
wsC.freeze_panes = "B2"
add_table(wsC, "Conversations", f"A1:Q{C_LAST}")
wsC["O1"].comment = Comment("Weighted total 0–100 from your answers in Manual scoring, using the checklist rules: N/A weight is redistributed, rounded to 2 decimals. Appears once all 8 criteria have an answer; n/a if no criterion applies.", "CQA")
wsC["J1"].comment = Comment("Seconds from the first to the last message.", "CQA")

# ---------------- Checklist ----------------
wsK = wb.create_sheet("Checklist")
kcols = ["Criterion ID", "Criterion", "Section", "Answer type", "Weight", "N/A allowed", "N/A condition", "Scale steps", "Top step", "What it checks"]
header(wsK, 1, kcols, [11, 34, 26, 11, 8, 10, 34, 14, 9, 80])
for k, c in enumerate(CHECKLIST):
    r = k + 2
    for j, v in enumerate(c[:8], 1):
        wsK.cell(r, j, v)
    wsK.cell(r, 9, int(c[7].split(",")[0]) if c[7] else (1 if c[3] != "flag" else 0))
    wsK.cell(r, 10, c[8])
    for col in range(1, 11):
        wsK.cell(r, col).font = F(color="0000FF") if col in (5, 9) else F()
        wsK.cell(r, col).alignment = WRAP
K_LAST = len(CHECKLIST) + 1
wsK.freeze_panes = "C2"
add_table(wsK, "Checklist", f"A1:J{K_LAST}")
wsK["E1"].comment = Comment("Weights from the official checklist PDF (Freedom Broker CQA hackathon). Change them here and every manual score recalculates.", "CQA")
wsK["I1"].comment = Comment("The step that earns the full weight. Yes/No criteria use 1 (Yes = full weight).", "CQA")
wsK.cell(K_LAST + 2, 1, "Weight total").font = F(bold=True)
wsK.cell(K_LAST + 2, 5, f"=SUM(E2:E{K_LAST})").font = F(bold=True)
wsK.cell(K_LAST + 3, 1, "Source: CQA Chat Quality Checklist PDF supplied by the organizers. Blue numbers are inputs; weights need not sum to 100 because scores are rescaled to 100.").font = F(italic=True, color="546072")
KR = lambda col: f"Checklist!${col}$2:${col}${K_LAST}"

# ---------------- Manual scoring ----------------
wsS = wb.create_sheet("Manual scoring")
scols = ["Conversation ID", "Criterion ID", "Criterion", "Manual result", "AI result", "Match", "Evidence message #", "Evidence timestamp",
         "Error type", "Recommendation", "Answer type", "Applicable weight", "Max points", "Points",
         "AI result (re-run)", "Match (re-run)", "Sample", "Manual note"]
header(wsS, 1, scols, [15, 10, 32, 13, 11, 8, 12, 24, 22, 44, 11, 11, 10, 9, 13, 10, 11, 60])
rows_by_crit = {c[0]: [] for c in CHECKLIST}
r = 2
for rec in recs:
    for c in CHECKLIST:
        cid = rec["conversation_id"]
        wsS.cell(r, 1, cid); wsS.cell(r, 2, c[0])
        wsS.cell(r, 3, f'=INDEX({KR("B")},MATCH(B{r},{KR("A")},0))')
        wsS.cell(r, 6, f'=IF(OR(D{r}="",E{r}=""),"",IF(D{r}&""=E{r}&"","Yes","No"))')
        wsS.cell(r, 8, f'=IF(G{r}="","",IFERROR(INDEX({MR("D")},MATCH(A{r}&"#"&G{r},{MR("A")},0)),"not found"))')
        wsS.cell(r, 11, f'=INDEX({KR("D")},MATCH(B{r},{KR("A")},0))')
        # weight counts only when answered, not N/A, and not the gross-error flag
        wsS.cell(r, 12, f'=IF(OR(D{r}="",D{r}="NA",K{r}="flag"),0,INDEX({KR("E")},MATCH(B{r},{KR("A")},0)))')
        wsS.cell(r, 13, f'=IF(L{r}=0,0,L{r}*100/SUMIFS($L$2:$L${SC_LAST},$A$2:$A${SC_LAST},A{r}))')
        wsS.cell(r, 14, f'=IF(M{r}=0,0,M{r}*IF(K{r}="scale",IFERROR(D{r}/INDEX({KR("I")},MATCH(B{r},{KR("A")},0)),0),IF(D{r}="YES",1,0)))')
        wsS.cell(r, 16, f'=IF(OR(D{r}="",O{r}=""),"",IF(D{r}&""=O{r}&"","Yes","No"))')
        wsS.cell(r, 17, SAMPLE.get(cid, ""))
        rows_by_crit[c[0]].append(r)
        r += 1
assert r - 1 == SC_LAST
for row in wsS.iter_rows(min_row=2, max_row=SC_LAST):
    for c in row:
        c.font = F(); c.alignment = TOP
        if c.column in (4, 5, 7, 9, 10, 15, 18):
            c.fill = INPUT_FILL
    row[9].alignment = WRAP; row[17].alignment = WRAP
for col in "MN":
    for rr in range(2, SC_LAST + 1):
        wsS[f"{col}{rr}"].number_format = "0.00"
wsS.freeze_panes = "D2"
add_table(wsS, "ManualScoring", f"A1:R{SC_LAST}")

# drop-downs per criterion: allowed answers depend on the criterion
for c in CHECKLIST:
    opts = [s.strip() for s in c[7].split(",")] if c[3] == "scale" else list(VALID[c[3]])
    if c[5] == "Yes": opts.append("NA")
    dv = DataValidation(type="list", formula1='"' + ",".join(opts) + '"', allow_blank=True, showErrorMessage=True,
                        errorTitle="Not an allowed answer", error=f"{c[0]} accepts: {', '.join(opts)}")
    dv.sqref = " ".join(f"D{x}:E{x} O{x}" for x in rows_by_crit[c[0]])
    wsS.add_data_validation(dv)
err_types = ["Wrong answer", "Wrong N/A", "Wrong scale step", "Bad evidence", "Bot confused", "Language", "False critical", "Missed critical"]
dv = DataValidation(type="list", formula1='"' + ",".join(err_types) + '"', allow_blank=True)
dv.sqref = f"I2:I{SC_LAST}"; wsS.add_data_validation(dv)
dv = DataValidation(type="whole", operator="between", formula1="0", formula2="500", allow_blank=True, showErrorMessage=True,
                    error="Enter the message number shown in the app (#0, #1 …).")
dv.sqref = f"G2:G{SC_LAST}"; wsS.add_data_validation(dv)
wsS["D1"].comment = Comment("Your answer: YES, NO, NA or a scale step. The drop-down shows only what this criterion allows.", "CQA")
wsS["E1"].comment = Comment("The AI's answer for the same criterion, copied from the app or the backend.", "CQA")
wsS["G1"].comment = Comment("Message number of the evidence, counting from 0 as in the app.", "CQA")
wsS["L1"].comment = Comment("Checklist weight if the criterion is answered and applies; 0 for N/A, blank answers and the gross-error flag.", "CQA")
wsS["M1"].comment = Comment("Weight rescaled so that the applicable criteria of this conversation add up to 100 (N/A redistribution).", "CQA")

# ---------------- Summary ----------------
wsY = wb.create_sheet("Summary", 0)
wsY.column_dimensions["A"].width = 30
for col in "BCDEF": wsY.column_dimensions[col].width = 13
wsY["A1"] = f"CQA dataset {group}: summary"; wsY["A1"].font = TITLE
wsY["A2"] = "Every number below is a formula over the other sheets and updates as you score."; wsY["A2"].font = F(italic=True, color="546072")
CR = lambda col: f"Conversations!${col}$2:${col}${C_LAST}"

def block_header(row, cols):
    for i, v in enumerate(cols):
        c = wsY.cell(row, 1 + i, v); c.font = HEAD_FONT; c.fill = HEAD_FILL

block_header(4, ["Conversations", "Chat", "Call", "Total"])
for k, lang in enumerate(["RU", "KZ", "EN"]):
    r = 5 + k
    wsY.cell(r, 1, lang)
    wsY.cell(r, 2, f'=COUNTIFS({CR("B")},"{lang}",{CR("D")},"Chat")')
    wsY.cell(r, 3, f'=COUNTIFS({CR("B")},"{lang}",{CR("D")},"Call")')
    wsY.cell(r, 4, f"=B{r}+C{r}")
wsY.cell(8, 1, "Total").font = F(bold=True)
for col in "BCD":
    wsY[f"{col}8"] = f"=SUM({col}5:{col}7)"; wsY[f"{col}8"].font = F(bold=True)

block_header(10, ["Messages", "Count", "Share"])
for k, sp in enumerate(["Client", "Operator", "Bot", "System"]):
    r = 11 + k
    wsY.cell(r, 1, sp)
    wsY.cell(r, 2, f'=COUNTIF({MR("F")},"{sp}")')
    wsY.cell(r, 3, f"=IF($B$15=0,0,B{r}/$B$15)"); wsY.cell(r, 3).number_format = "0.0%"
wsY["A15"] = "Total"; wsY["A15"].font = F(bold=True)
wsY["B15"] = "=SUM(B11:B14)"; wsY["B15"].font = F(bold=True)
wsY["A16"] = "Messages per conversation (average)"
wsY["B16"] = f"=IF(D8=0,0,B15/D8)"; wsY["B16"].number_format = "0.0"
wsY["A17"] = "Longest conversation (messages)"
wsY["B17"] = f"=MAX({CR('E')})"

block_header(19, ["Manual scoring", "Value"])
labels = [
    ("Answers entered", f'=COUNTA({SR("D")})', "0"),
    ("Answers needed", f"={len(recs) * len(CHECKLIST)}", "0"),
    ("Progress", "=IF(B21=0,0,B20/B21)", "0.0%"),
    ("Conversations fully scored", f'=COUNTIFS({CR("P")},{len(CHECKLIST)})', "0"),
    ("Average manual score", f'=IFERROR(AVERAGEIFS({CR("O")},{CR("P")},{len(CHECKLIST)}),"")', "0.00"),
    ("Gross violations found", f'=COUNTIF({CR("Q")},"Yes")', "0"),
    ("AI answers compared", f'=COUNTIF({SR("F")},"Yes")+COUNTIF({SR("F")},"No")', "0"),
    ("AI agreement with manual", f'=IF(B26=0,"",COUNTIF({SR("F")},"Yes")/B26)', "0.0%"),
]
for k, (lab, f, fmt) in enumerate(labels):
    r = 20 + k
    wsY.cell(r, 1, lab); wsY.cell(r, 2, f).number_format = fmt
wsY["B21"].comment = Comment(f"{len(recs)} conversations × {len(CHECKLIST)} criteria.", "CQA")

block_header(29, ["Agreement by criterion", "Compared", "Agree", "Agreement", "N/A (manual)", "Agreement (re-run)"])
for k, c in enumerate(CHECKLIST):
    r = 30 + k
    wsY.cell(r, 1, f"{c[0]} {c[1]}")
    wsY.cell(r, 2, f'=COUNTIFS({SR("B")},"{c[0]}",{SR("F")},"Yes")+COUNTIFS({SR("B")},"{c[0]}",{SR("F")},"No")')
    wsY.cell(r, 3, f'=COUNTIFS({SR("B")},"{c[0]}",{SR("F")},"Yes")')
    wsY.cell(r, 4, f'=IF(B{r}=0,"",C{r}/B{r})'); wsY.cell(r, 4).number_format = "0.0%"
    wsY.cell(r, 5, f'=COUNTIFS({SR("B")},"{c[0]}",{SR("D")},"NA")')
    yes2, no2 = (f'COUNTIFS({SR("B")},"{c[0]}",{SR("P")},"{v}")' for v in ("Yes", "No"))
    wsY.cell(r, 6, f'=IF({yes2}+{no2}=0,"",{yes2}/({yes2}+{no2}))'); wsY.cell(r, 6).number_format = "0.0%"
wsY.column_dimensions["F"].width = 18

# agreement with the manual sample, split by working and held-out dialogues
SB = 40
block_header(SB, ["Agreement with manual sample", "First run", "Re-run", "Compared (first)", "Compared (re-run)"])
def agree(grp, col):
    y = "+".join(f'COUNTIFS({SR("Q")},"{g}",{SR(col)},"Yes")' for g in grp)
    n = "+".join(f'COUNTIFS({SR("Q")},"{g}",{SR(col)},"No")' for g in grp)
    return f'=IF(({y})+({n})=0,"",({y})/(({y})+({n})))', f'=({y})+({n})'
for k, (lab, grp) in enumerate([("Working (12 dialogues)", ["Working"]), ("Held-out (8 dialogues)", ["Held-out"]), ("All 20", ["Working", "Held-out"])]):
    r = SB + 1 + k
    wsY.cell(r, 1, lab)
    f1, c1 = agree(grp, "F"); f2, c2 = agree(grp, "P")
    wsY.cell(r, 2, f1).number_format = "0.0%"; wsY.cell(r, 3, f2).number_format = "0.0%"
    wsY.cell(r, 4, c1); wsY.cell(r, 5, c2)
wsY.cell(SB + 4, 1, "Say “agreement with our manual sample”, not “accuracy”: the sample is internal, not the organizers' ground truth.").font = F(italic=True, color="546072")
for row in wsY.iter_rows(min_row=4, max_row=SB + 3):
    for c in row:
        if c.fill != HEAD_FILL and not c.font.bold:
            c.font = F()
        elif c.fill != HEAD_FILL:
            c.font = F(bold=True)

# ---------------- README ----------------
wsR = wb.create_sheet("How to use", 0)
wsR.column_dimensions["A"].width = 22; wsR.column_dimensions["B"].width = 95
wsR["A1"] = f"CQA dataset {group}"; wsR["A1"].font = TITLE
wsR["A2"] = f"{len(recs)} conversations and {M_LAST - 1} messages from the organizers' API (group_id={group}, isSuccess={raw.get('isSuccess')})."
wsR["A2"].font = F(color="546072")
rows = [
    ("Sheet", "What it holds"),
    ("Summary", "Counts by language, channel and speaker, scoring progress and AI agreement. All formulas."),
    ("Conversations", "One row per conversation: language, channel, message counts, duration, the client's first message, audio link and your manual score."),
    ("Messages", "Every message with its number (#0, #1 … as in the app), the original timestamp, seconds from the start, speaker and text. Filter by Conversation ID to read one dialogue."),
    ("Checklist", "The 8 criteria from the checklist PDF with weights, N/A rules and scale steps. Blue cells are inputs."),
    ("Manual scoring", "One row per conversation and criterion (520 rows). Fill in the yellow columns; the rest is calculated."),
    ("", ""),
    ("What to fill in", "Only the yellow cells in Manual scoring: Manual result, AI result, Evidence message #, Error type and Recommendation."),
    ("", "Manual result and AI result have drop-downs with the answers each criterion allows: YES, NO, NA or a scale step."),
    ("", "Match, Evidence timestamp, Max points and Points fill in by themselves. Conversations › Manual score appears once all 8 criteria have an answer."),
    ("", ""),
    ("Example row", "Conversation ID p1-ru-001, Criterion ID c7, Manual result NO, AI result YES, Evidence message # 9, Error type Wrong answer. "
                    "Recommendation: “The operator closed the chat without a goodbye; add a negative example to the prompt.”"),
    ("", "Match then shows No and Evidence timestamp shows the time of message #9."),
    ("", ""),
    ("Scoring rules", "Yes gives the full weight, No gives 0, a scale step gives step ÷ top step × weight. N/A criteria are dropped and the remaining weights are rescaled to 100. "
                      "c8 (gross errors) gives no points and is shown separately. These follow the checklist PDF, page 2."),
    ("Not official", "Manual scores here are an internal expert sample, not the organizers' ground truth."),
]
for k, (a, b) in enumerate(rows):
    r = 4 + k
    wsR.cell(r, 1, a).font = HEAD_FONT if k == 0 else F(bold=bool(a))
    wsR.cell(r, 2, b).font = HEAD_FONT if k == 0 else F()
    if k == 0:
        wsR.cell(r, 1).fill = HEAD_FILL; wsR.cell(r, 2).fill = HEAD_FILL
    wsR.cell(r, 2).alignment = WRAP
wsR["A22"] = "Yellow cells"; wsR["A22"].font = F(bold=True); wsR["B22"] = "are the ones to edit."; wsR["B22"].fill = INPUT_FILL; wsR["B22"].font = F()

wb.active = 0
wb.save(OUT)
print("saved", OUT, "messages", M_LAST - 1, "scoring rows", SC_LAST - 1)
