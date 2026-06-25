
cat > /home/claude/alarmiq_v2.py << 'PYEOF'
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, Image, HRFlowable
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY, TA_RIGHT
import io

# ── PAGE SETUP ──────────────────────────────────────────────────────────────
W, H = A4
LEFT = RIGHT = 0.85 * inch
TOP  = BOT   = 0.75 * inch
BODY_W = W - LEFT - RIGHT          # usable width ≈ 6.53 inch

# ── COLOURS ─────────────────────────────────────────────────────────────────
NAVY  = colors.HexColor('#0d1b2a')
BLUE  = colors.HexColor('#1565c0')
CYAN  = colors.HexColor('#0288d1')
TEAL  = colors.HexColor('#006064')
WHITE = colors.white
LGRAY = colors.HexColor('#e8edf2')
MGRAY = colors.HexColor('#b0bec5')
DTEXT = colors.HexColor('#212121')
RED   = colors.HexColor('#c62828')
GREEN = colors.HexColor('#2e7d32')
AMBER = colors.HexColor('#e65100')
LBLUE = colors.HexColor('#e3f2fd')

# ── STYLES ───────────────────────────────────────────────────────────────────
_ss = getSampleStyleSheet()

def S(name, **kw):
    base = kw.pop('base', 'Normal')
    return ParagraphStyle(name, parent=_ss[base], **kw)

# paragraph styles
ST_DOC_TITLE = S('DocTitle', fontSize=22, textColor=NAVY, leading=28,
                  alignment=TA_CENTER, fontName='Helvetica-Bold', spaceAfter=4)
ST_DOC_SUB   = S('DocSub',   fontSize=11, textColor=CYAN, leading=15,
                  alignment=TA_CENTER, fontName='Helvetica', spaceAfter=2)
ST_SEC       = S('Sec',      fontSize=12, textColor=WHITE, leading=16,
                  fontName='Helvetica-Bold', alignment=TA_LEFT,
                  leftIndent=4, spaceAfter=0, spaceBefore=0)
ST_SUBSEC    = S('Subsec',   fontSize=11, textColor=NAVY, leading=15,
                  fontName='Helvetica-Bold', spaceAfter=3, spaceBefore=8)
ST_BODY      = S('Body',     fontSize=9,  textColor=DTEXT, leading=14,
                  fontName='Helvetica', spaceAfter=0, alignment=TA_LEFT)
ST_BULL      = S('Bull',     fontSize=9,  textColor=DTEXT, leading=13,
                  fontName='Helvetica', leftIndent=10, spaceAfter=1)
ST_SUBBULL   = S('Subbull',  fontSize=8.5,textColor=DTEXT, leading=12,
                  fontName='Helvetica', leftIndent=20, spaceAfter=1)
ST_CAP       = S('Cap',      fontSize=8,  textColor=colors.HexColor('#555555'),
                  alignment=TA_CENTER, fontName='Helvetica-Oblique', spaceAfter=6)
ST_EQ        = S('Eq',       fontSize=9.5,textColor=NAVY, leading=14,
                  fontName='Courier-Bold', alignment=TA_CENTER,
                  spaceBefore=4, spaceAfter=4)
ST_TH        = S('TH',       fontSize=8.5,textColor=WHITE, leading=12,
                  fontName='Helvetica-Bold', alignment=TA_CENTER)
ST_TD        = S('TD',       fontSize=8.5,textColor=DTEXT, leading=12,
                  fontName='Helvetica', alignment=TA_LEFT)
ST_TDC       = S('TDC',      fontSize=8.5,textColor=DTEXT, leading=12,
                  fontName='Helvetica', alignment=TA_CENTER)
ST_HIGHLIGHT = S('HL',       fontSize=9,  textColor=WHITE, leading=14,
                  fontName='Helvetica-Bold', alignment=TA_JUSTIFY)
ST_NOTE      = S('Note',     fontSize=8.5,textColor=colors.HexColor('#4a148c'),
                  leading=13, fontName='Helvetica', alignment=TA_JUSTIFY)
ST_COVER_KEY = S('CK',       fontSize=9.5,textColor=NAVY, leading=14,
                  fontName='Helvetica-Bold')
ST_COVER_VAL = S('CV',       fontSize=9.5,textColor=DTEXT, leading=14,
                  fontName='Helvetica')

# ── HELPER: wrap text for table cells ────────────────────────────────────────
def th(text): return Paragraph(text, ST_TH)
def td(text): return Paragraph(text, ST_TD)
def tdc(text): return Paragraph(text, ST_TDC)
def bp(text): return Paragraph(f'• {text}', ST_BULL)
def sbp(text): return Paragraph(f'– {text}', ST_SUBBULL)
def sp(h=6): return Spacer(1, h)
def hr(): return HRFlowable(width='100%', thickness=0.4, color=MGRAY, spaceAfter=4, spaceBefore=4)
def caption(t): return Paragraph(t, ST_CAP)
def eq(t): return Paragraph(t, ST_EQ)
def note(t): return [
    Table([[Paragraph(f'<b>Note:</b> {t}', ST_NOTE)]],
          colWidths=[BODY_W],
          style=TableStyle([
              ('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#f3e5f5')),
              ('BOX',(0,0),(-1,-1),0.8,colors.HexColor('#7b1fa2')),
              ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
              ('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),
          ])),
    sp(5)
]

def section_hdr(number, title):
    label = f'{number}.  {title}'
    return [
        sp(10),
        Table([[Paragraph(label, ST_SEC)]],
              colWidths=[BODY_W],
              style=TableStyle([
                  ('BACKGROUND',(0,0),(-1,-1),NAVY),
                  ('TOPPADDING',(0,0),(-1,-1),7),
                  ('BOTTOMPADDING',(0,0),(-1,-1),7),
                  ('LEFTPADDING',(0,0),(-1,-1),10),
                  ('RIGHTPADDING',(0,0),(-1,-1),10),
              ])),
        sp(7),
    ]

def subsection(title):
    return [Paragraph(title, ST_SUBSEC), sp(3)]

def highlight_box(text, bg=NAVY, border=CYAN):
    return [
        Table([[Paragraph(text, ST_HIGHLIGHT)]],
              colWidths=[BODY_W],
              style=TableStyle([
                  ('BACKGROUND',(0,0),(-1,-1),bg),
                  ('BOX',(0,0),(-1,-1),1.5,border),
                  ('TOPPADDING',(0,0),(-1,-1),10),
                  ('BOTTOMPADDING',(0,0),(-1,-1),10),
                  ('LEFTPADDING',(0,0),(-1,-1),12),
                  ('RIGHTPADDING',(0,0),(-1,-1),12),
              ])),
        sp(6)
    ]

# ── BASE TABLE STYLE ─────────────────────────────────────────────────────────
def tbl_style(hbg=NAVY, alt=LGRAY, align='CENTER'):
    return TableStyle([
        # header row
        ('BACKGROUND',    (0,0), (-1,0),  hbg),
        ('TEXTCOLOR',     (0,0), (-1,0),  WHITE),
        # alternating rows
        ('ROWBACKGROUNDS',(0,1), (-1,-1), [WHITE, alt]),
        # grid
        ('GRID',          (0,0), (-1,-1), 0.4, MGRAY),
        ('LINEBELOW',     (0,0), (-1,0),  1.0, hbg),
        # padding
        ('TOPPADDING',    (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING',   (0,0), (-1,-1), 6),
        ('RIGHTPADDING',  (0,0), (-1,-1), 6),
        # alignment
        ('VALIGN',        (0,0), (-1,-1), 'TOP'),
        ('ALIGN',         (0,0), (-1,0),  'CENTER'),
    ])

# ── CHART HELPERS ────────────────────────────────────────────────────────────
def fig_to_image(fig, w_inch, h_inch):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=160, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)
    buf.seek(0)
    return Image(buf, width=w_inch*inch, height=h_inch*inch)

def pie_chart(labels, sizes, title, palette, w=3.8, h=3.0):
    fig, ax = plt.subplots(figsize=(w, h))
    wedges, texts, autos = ax.pie(
        sizes, labels=None, autopct='%1.1f%%',
        colors=palette, startangle=140,
        pctdistance=0.72,
        wedgeprops=dict(edgecolor='white', linewidth=1.2))
    for a in autos:
        a.set_fontsize(7); a.set_color('white'); a.set_fontweight('bold')
    ax.legend(wedges, labels, loc='lower center',
              bbox_to_anchor=(0.5, -0.28), ncol=2,
              fontsize=6.5, framealpha=0.9, handlelength=1)
    ax.set_title(title, fontsize=9, fontweight='bold', color='#0d1b2a', pad=6)
    fig.tight_layout()
    return fig_to_image(fig, w, h)

def hbar_chart(cats, vals, title, color='#1565c0', w=6.0, h=3.2):
    fig, ax = plt.subplots(figsize=(w, h))
    y = range(len(cats))
    bars = ax.barh(y, vals, color=color, edgecolor='white', linewidth=0.5, height=0.55)
    ax.set_yticks(list(y)); ax.set_yticklabels(cats, fontsize=7.5)
    ax.set_title(title, fontsize=9, fontweight='bold', color='#0d1b2a')
    ax.set_xlabel('Count', fontsize=8)
    ax.tick_params(axis='x', labelsize=7.5)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.set_facecolor('#f5f7fa')
    for bar, v in zip(bars, vals):
        ax.text(bar.get_width() + max(vals)*0.01, bar.get_y()+bar.get_height()/2,
                f'{v:,}', va='center', fontsize=6.5, color='#333')
    fig.tight_layout()
    return fig_to_image(fig, w, h)

def timeline_chart(months, vals, title, w=6.0, h=2.8):
    fig, ax = plt.subplots(figsize=(w, h))
    x = range(len(months))
    ax.fill_between(x, vals, alpha=0.18, color='#1565c0')
    ax.plot(x, vals, color='#1565c0', lw=2, marker='o', markersize=4)
    ax.set_xticks(list(x)); ax.set_xticklabels(months, rotation=40, ha='right', fontsize=7)
    ax.set_title(title, fontsize=9, fontweight='bold', color='#0d1b2a')
    ax.set_ylabel('Alarms', fontsize=8); ax.tick_params(axis='y', labelsize=7.5)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.set_facecolor('#f5f7fa')
    fig.tight_layout()
    return fig_to_image(fig, w, h)

def markov_diagram(w=6.0, h=3.6):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0,10); ax.set_ylim(0,6); ax.axis('off')
    states = [('NORMAL',2,4.2,'#2e7d32'),('DEGRADED',5,5,'#e65100'),
              ('CRITICAL',8,4.2,'#c62828'),('DOWN',5,1.5,'#4a148c')]
    for name,x,y,c in states:
        circ = plt.Circle((x,y), 0.85, color=c, alpha=0.88, zorder=3)
        ax.add_patch(circ)
        ax.text(x,y, name, ha='center', va='center', fontsize=7.5,
                fontweight='bold', color='white', zorder=4)
    arrows = [
        (2,4.2, 5,5,   '0.30','above'),
        (5,5,   8,4.2, '0.40','above'),
        (8,4.2, 5,1.5, '0.60','right'),
        (5,1.5, 2,4.2, '0.50','left'),
        (5,5,   2,4.2, '0.30','below'),
        (8,4.2, 2,4.2, '0.10','below'),
    ]
    for x1,y1,x2,y2,lbl,pos in arrows:
        ax.annotate('', xy=(x2,y2), xytext=(x1,y1),
            arrowprops=dict(arrowstyle='->', color='#37474f', lw=1.4,
                            connectionstyle='arc3,rad=0.18'), zorder=2)
        mx,my = (x1+x2)/2, (y1+y2)/2
        off = 0.22 if pos in ('above','right') else -0.22
        ax.text(mx, my+off, lbl, ha='center', va='center',
                fontsize=7, color='#1565c0', fontweight='bold',
                bbox=dict(boxstyle='round,pad=0.15', fc='white', ec='none', alpha=0.8))
    ax.set_title('Markov Chain — Network Node State Transitions',
                 fontsize=9, fontweight='bold', color='#0d1b2a', pad=6)
    fig.tight_layout()
    return fig_to_image(fig, w, h)

# ═══════════════════════════════════════════════════════════════════════════════
# BUILD PDF
# ═══════════════════════════════════════════════════════════════════════════════
def build():
    out = '/mnt/user-data/outputs/AlarmIQ_Report_v2.pdf'
    doc = SimpleDocTemplate(out, pagesize=A4,
        leftMargin=LEFT, rightMargin=RIGHT,
        topMargin=TOP, bottomMargin=BOT)
    story = []

    # ── COLUMN WIDTH CONSTANTS ──────────────────────────────────────────────
    # define reusable column sets (always sum to BODY_W)
    C2_EQ  = [BODY_W*0.38, BODY_W*0.62]
    C2_NAR = [BODY_W*0.30, BODY_W*0.70]
    C2_WIDE= [BODY_W*0.42, BODY_W*0.58]
    C3_EQ  = [BODY_W/3,    BODY_W/3,    BODY_W/3]
    C3_SML = [BODY_W*0.15, BODY_W*0.28, BODY_W*0.57]
    C3_MED = [BODY_W*0.22, BODY_W*0.22, BODY_W*0.56]
    C4_EQ  = [BODY_W/4]*4
    C4_MED = [BODY_W*0.12, BODY_W*0.28, BODY_W*0.28, BODY_W*0.32]

    # ══════════════════════════════════════════════════════
    # COVER PAGE
    # ══════════════════════════════════════════════════════
    story.append(sp(30))
    story.append(Table([[Paragraph('BSNL INTERNSHIP PROJECT REPORT', S('badge',
        fontSize=9, textColor=WHITE, fontName='Helvetica-Bold', alignment=TA_CENTER))]],
        colWidths=[BODY_W],
        style=TableStyle([('BACKGROUND',(0,0),(-1,-1),CYAN),
            ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)])))
    story.append(sp(18))
    story.append(Paragraph('AlarmIQ', ST_DOC_TITLE))
    story.append(Paragraph('Telecom Alarm Intelligence &amp; Early Warning System', ST_DOC_SUB))
    story.append(sp(6))
    story.append(HRFlowable(width='100%', thickness=1.2, color=BLUE, spaceAfter=14))

    cover = [
        [Paragraph('Student',       ST_COVER_KEY), Paragraph('ALESTOR ALDOUS', ST_COVER_VAL)],
        [Paragraph('College',       ST_COVER_KEY), Paragraph('Model Engineering College', ST_COVER_VAL)],
        [Paragraph('Degree',        ST_COVER_KEY), Paragraph('B.E. Computer Science Engineering — 3rd Year', ST_COVER_VAL)],
        [Paragraph('Organization',  ST_COVER_KEY), Paragraph('Bharat Sanchar Nigam Limited (BSNL)', ST_COVER_VAL)],
        [Paragraph('Project',       ST_COVER_KEY), Paragraph('Data Science Internship Project', ST_COVER_VAL)],
        [Paragraph('Dataset',       ST_COVER_KEY), Paragraph('296,000 Alarm Records | 875 Days | 572 Nodes', ST_COVER_VAL)],
        [Paragraph('Report Type',   ST_COVER_KEY), Paragraph('Technical Internship & Placement Report', ST_COVER_VAL)],
    ]
    story.append(Table(cover, colWidths=[BODY_W*0.28, BODY_W*0.72],
        style=TableStyle([
            ('FONTNAME',(0,0),(-1,-1),'Helvetica'),
            ('ROWBACKGROUNDS',(0,0),(-1,-1),[WHITE, LGRAY]),
            ('GRID',(0,0),(-1,-1),0.4,MGRAY),
            ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
            ('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),
            ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ])))
    story.append(sp(16))
    story += highlight_box(
        'This report covers the complete AlarmIQ project developed during a BSNL internship. '
        'It includes all 22 data science pipeline stages, real network alarm statistics, '
        'mathematical analysis with equations, visualisations, and a worked Markov Chain problem — '
        'all explained in clear, simple language.')
    story.append(PageBreak())

    # ══════════════════════════════════════════════════════
    # 1. ABOUT BSNL
    # ══════════════════════════════════════════════════════
    story += section_hdr('1', 'About BSNL & India\'s Telecom Infrastructure')
    story.append(bp('<b>BSNL (Bharat Sanchar Nigam Limited)</b> is India\'s largest government-owned telecom operator, established in 2000.'))
    story.append(bp('It operates the largest terrestrial telecom network in India, present in all <b>22 telecom circles</b>.'))
    story.append(bp('Services offered: Voice, Broadband, MPLS VPN, Leased Lines, Satellite, and Mobile.'))
    story.append(bp('Role: "Carrier of Carriers" — provides backhaul to private ISPs and enterprise customers.'))
    story.append(bp('Manages a nationwide <b>fibre-optic backbone</b> connecting towns, cities, and remote regions.'))
    story.append(sp(6))

    infra = [
        [th('Infrastructure Component'), th('Description')],
        [td('BTS (Base Transceiver Station)'),      td('Thousands of BTS nodes across India for mobile radio connectivity')],
        [td('PE Routers (Provider Edge Routers)'),  td('Hundreds deployed at the edge of the MPLS core network')],
        [td('Hardware — Cisco ASR 9000 Series'),    td('Enterprise-grade routers used as PE routers in BSNL\'s MPLS network')],
        [td('EPNM (Evolved Programmable\nNetwork Manager)'), td('Cisco\'s NMS (Network Management System) used by BSNL to monitor alarms')],
        [td('NMS / OMCR'),                          td('NMS (Network Management System) / OMCR (Operations & Maintenance Centre for Radio) — BSNL internal systems')],
        [td('Telecom Circles'),                     td('22 circles covering the entire country including rural and remote regions')],
    ]
    story.append(Table(infra, colWidths=[BODY_W*0.38, BODY_W*0.62], style=tbl_style()))
    story.append(caption('Table 1.1 — BSNL Infrastructure Overview'))
    story.append(sp(4))

    # ══════════════════════════════════════════════════════
    # 2. INDIA'S MOBILE NETWORK
    # ══════════════════════════════════════════════════════
    story += section_hdr('2', 'How India\'s Mobile Network Works')
    story.append(bp('A mobile network carries your call or data from your phone to the internet in several hops.'))
    story.append(bp('Each layer has a specific job — from radio signals to high-speed data forwarding.'))
    story.append(sp(6))

    flow = [
        [th('Layer'), th('Component'), th('Full Form'), th('What It Does')],
        [tdc('1'), td('UE'),       td('User Equipment'),                      td('Your mobile phone — connects to the network over radio waves')],
        [tdc('2'), td('BTS'),      td('Base Transceiver Station'),             td('The tower near you — sends and receives your radio signal')],
        [tdc('3'), td('BSC / RNC'),td('Base Station Controller / Radio Network Controller'), td('Controls several BTS towers and manages radio resources')],
        [tdc('4'), td('MSC'),      td('Mobile Switching Center'),              td('Routes voice calls — works like a telephone exchange')],
        [tdc('5'), td('MPLS Core'),td('Multiprotocol Label Switching Core'),   td('High-speed backbone — forwards packets using labels, not IP addresses')],
        [tdc('6'), td('PE Router'),td('Provider Edge Router'),                 td('Gateway between BSNL\'s core and the customer\'s network')],
        [tdc('7'), td('Core Router'),td('—'),                                  td('Heart of the backbone — carries all aggregated traffic')],
        [tdc('8'), td('Internet / PSTN'), td('Public Switched Telephone Network'), td('Final destination — the internet or fixed phone line')],
    ]
    cw_flow = [BODY_W*0.06, BODY_W*0.10, BODY_W*0.30, BODY_W*0.54]
    story.append(Table(flow, colWidths=cw_flow, style=tbl_style()))
    story.append(caption('Table 2.1 — Mobile Network Layers: from UE to Internet'))
    story.append(sp(6))

    # text-art flow diagram
    diag = [
        ['[Mobile Phone / UE]'],
        ['↓  Radio Signal'],
        ['[BTS — Base Transceiver Station]'],
        ['↓  Wired Backhaul'],
        ['[BSC / RNC — Base Station Controller]'],
        ['↓'],
        ['[MSC — Mobile Switching Center]'],
        ['↓'],
        ['[BSNL MPLS Core Network]'],
        ['PE Router  ←→  Core Router  ←→  PE Router'],
        ['↓'],
        ['[PSTN / Internet / Enterprise VPN]'],
    ]
    diag_style = S('DS', fontSize=9, textColor=CYAN, fontName='Courier-Bold',
                   alignment=TA_CENTER, leading=13)
    story.append(Table([[Paragraph(r[0], diag_style)] for r in diag],
        colWidths=[BODY_W],
        style=TableStyle([
            ('BACKGROUND',(0,0),(-1,-1), colors.HexColor('#0d1b2a')),
            ('TOPPADDING',(0,0),(-1,-1), 3), ('BOTTOMPADDING',(0,0),(-1,-1), 3),
            ('LEFTPADDING',(0,0),(-1,-1), 10),('RIGHTPADDING',(0,0),(-1,-1), 10),
        ])))
    story.append(caption('Figure 2.1 — India Mobile Network Flow Diagram'))

    story.append(sp(6))
    story += note('Think of it like a postal system: your phone is the letter, BTS is the local post office, '
                  'BSC is the regional sorting hub, MSC is the national dispatch center, and MPLS Core is '
                  'the express highway that delivers everything at high speed.')

    # ══════════════════════════════════════════════════════
    # 3. GLOSSARY
    # ══════════════════════════════════════════════════════
    story.append(PageBreak())
    story += section_hdr('3', 'Key Networking Technologies & Alarm Terminology')
    story.append(bp('All abbreviations are expanded on first use throughout this report.'))
    story.append(bp('The tables below explain every technical term in simple language.'))
    story.append(sp(6))

    # Protocols
    g1 = [
        [th('Abbreviation'), th('Full Form'), th('Simple Explanation')],
        [td('MPLS'),   td('Multiprotocol Label Switching'),         td('Attaches a short label to packets. Routers forward by label only — much faster than reading full IP addresses at every hop.')],
        [td('BGP'),    td('Border Gateway Protocol'),               td('The internet\'s routing language. iBGP = BSNL routers talking internally. eBGP = BSNL talking to other ISPs outside.')],
        [td('L3VPN'),  td('Layer 3 Virtual Private Network'),       td('A private encrypted tunnel over MPLS for enterprise customers — connects branch offices at the IP layer.')],
        [td('L2VPN'),  td('Layer 2 Virtual Private Network'),       td('A private tunnel at the Ethernet frame layer. Used for pseudowire circuits between enterprise sites.')],
        [td('OSPF'),   td('Open Shortest Path First'),              td('An interior routing protocol used inside one BSNL region to automatically find the best path between routers.')],
        [td('IS-IS'),  td('Intermediate System to Intermediate System'), td('Alternative interior routing protocol preferred in BSNL\'s MPLS core for its very fast failure recovery.')],
        [td('IGP'),    td('Interior Gateway Protocol'),             td('Umbrella term for routing protocols used inside one network. Both OSPF and IS-IS are IGPs.')],
        [td('SLA'),    td('Service Level Agreement'),               td('A contract between BSNL and a customer that guarantees minimum uptime and service quality.')],
    ]
    cw_g = [BODY_W*0.13, BODY_W*0.30, BODY_W*0.57]
    story.append(Table(g1, colWidths=cw_g, style=tbl_style()))
    story.append(caption('Table 3.1 — Core Networking Protocol Abbreviations'))
    story.append(sp(8))

    g2 = [
        [th('Term'), th('Full Form'), th('Simple Explanation')],
        [td('PE Router'),            td('Provider Edge Router'),                         td('Boundary router between BSNL\'s MPLS core and the customer network. Very critical — its failure impacts enterprise customers directly.')],
        [td('Core Router'),          td('—'),                                             td('The most central router in the backbone. A failure here can impact thousands of customers simultaneously.')],
        [td('Satellite Chassis'),    td('—'),                                             td('A remote line-card chassis connected to a host router via satellite link. Extends router capacity to remote locations.')],
        [td('SFP'),                  td('Small Form-factor Pluggable'),                  td('A small optical transceiver module that plugs into a router port to connect fibre-optic cables.')],
        [td('Optical Alarm'),        td('—'),                                             td('Alarm indicating signal loss on a fibre link. Usually caused by a broken fibre cable or a dirty/failed SFP module.')],
        [td('cefcPowerStatusChange'),td('Cisco Entity FRU Power Status Change'),         td('Hardware alarm saying a power supply module changed state. Indicates a potential hardware failure risk.')],
        [td('cefcFRURemoved'),       td('Cisco Entity Field Replaceable Unit Removed'),  td('Alarm triggered when a hardware module such as a line card, SFP, or power supply is physically removed from a router.')],
        [td('PW_UPDOWN'),            td('Pseudowire Up/Down'),                           td('Alarm saying a Layer 2 VPN pseudowire circuit went up or down. Directly impacts L2VPN enterprise customers.')],
        [td('NOC'),                  td('Network Operations Center'),                    td('The team of engineers who monitor the network 24/7 and respond to alarms in real time.')],
        [td('EPNM'),                 td('Evolved Programmable Network Manager'),         td('Cisco\'s NMS used by BSNL to monitor Cisco ASR 9000 routers and collect alarm data.')],
        [td('NMS'),                  td('Network Management System'),                    td('Software that collects, displays, and manages alarms from all network devices.')],
        [td('OMCR'),                 td('Operations & Maintenance Centre for Radio'),    td('BSNL\'s dedicated centre for managing radio and BTS operations.')],
    ]
    story.append(Table(g2, colWidths=cw_g, style=tbl_style()))
    story.append(caption('Table 3.2 — Hardware & Alarm Terminology'))

    # ══════════════════════════════════════════════════════
    # 4. PROBLEM STATEMENT
    # ══════════════════════════════════════════════════════
    story.append(PageBreak())
    story += section_hdr('4', 'Problem Statement — Why This Project Was Needed')
    story.append(bp('BSNL\'s nationwide network generates massive volumes of alarms in real time.'))
    story.append(bp('Engineers in the NOC (Network Operations Center) were completely overwhelmed by alarm floods.'))
    story.append(bp('The core problem: too much noise made it impossible to spot real failures quickly.'))
    story.append(sp(6))

    prob = [
        [th('Problem'), th('Simple Explanation')],
        [td('Alarm Flooding'),      td('Hundreds of alarms arrive per second during a network event — far too many for any engineer to read.')],
        [td('Duplicate Alerts'),    td('A single failure triggers alarms from multiple network layers at the same time — all saying the same thing.')],
        [td('Cascading Failures'),  td('One root cause (e.g., a hardware fault) triggers hundreds of secondary alarms across the network.')],
        [td('No Early Warning'),    td('Engineers only learn about outages AFTER customers are already affected — not before.')],
        [td('Manual Analysis'),     td('Engineers must read raw alarm text dumps manually — very slow and highly error-prone.')],
        [td('Full Alarm Fatigue'),  td('0 out of 296,000 alarms were acknowledged — engineers stopped paying attention to the system entirely.')],
    ]
    story.append(Table(prob, colWidths=[BODY_W*0.28, BODY_W*0.72], style=tbl_style(hbg=RED)))
    story.append(caption('Table 4.1 — Problems in BSNL\'s Existing Alarm System'))
    story.append(sp(6))
    story += highlight_box(
        '• Key Fact: 92.46% of 296,000 alarms were auto-cleared — only ~7.5% needed human attention.\n'
        '• Signal-to-Noise Ratio = 1 : 13 — for every 1 real alert, 13 were pure noise.\n'
        '• AlarmIQ was built specifically to collapse this noise and surface only the real signals.')

    # ══════════════════════════════════════════════════════
    # 5. DATASET STATISTICS
    # ══════════════════════════════════════════════════════
    story += section_hdr('5', 'Dataset Statistics — The Numbers Behind the Project')
    story.append(bp('Data was sourced from <b>Cisco EPNM (Evolved Programmable Network Manager)</b> — BSNL\'s primary network monitoring tool.'))
    story.append(bp('Exported as structured alarm dumps in tabular format covering 2.4 years of BSNL network history.'))
    story.append(sp(6))

    ds = [
        [th('Metric'),                  th('Value'),                          th('What It Means')],
        [td('Total Alarm Records'),     tdc('296,000'),                       td('Total alarm events recorded across the entire dataset')],
        [td('Time Span'),               tdc('875 days'),                      td('Jan 3, 2024 to May 27, 2026 — approximately 2.4 years')],
        [td('Unique Network Nodes'),    tdc('572'),                           td('Individual routers, BTS towers, and equipment units monitored')],
        [td('Unique Alarm Types'),      tdc('63'),                            td('63 distinct alarm conditions found across all records')],
        [td('Peak Single Day'),         tdc('104,149 alarms'),                td('May 25, 2026 — the busiest single day in the entire dataset')],
        [td('Mean Alarms per Day'),     tdc('338.3'),                         td('Average number of alarms generated each day')],
        [td('Acknowledged Alarms'),     tdc('0 (0%)'),                        td('Not a single alarm was acknowledged — confirms full alarm fatigue')],
        [td('Data Source'),             tdc('Cisco EPNM'),                    td('BSNL\'s Cisco Network Management System exports')],
    ]
    story.append(Table(ds, colWidths=[BODY_W*0.28, BODY_W*0.22, BODY_W*0.50], style=tbl_style()))
    story.append(caption('Table 5.1 — Dataset Overview'))
    story.append(sp(8))

    # 5.1 Severity
    story += subsection('5.1  Alarm Severity Distribution')
    story.append(bp('Each alarm is assigned a severity level by the network device.'))
    story.append(bp('CLEARED means the network resolved it automatically — no human action was needed.'))
    story.append(bp('CRITICAL means a severe failure occurred — immediate engineer response required.'))
    story.append(sp(6))

    sev = [
        [th('Severity'),   th('Count'),    th('Percentage'), th('Meaning')],
        [td('CLEARED'),    tdc('273,684'), tdc('92.46 %'),   td('Alarm resolved automatically — safe to deprioritise')],
        [td('MAJOR'),      tdc('11,926'),  tdc('4.03 %'),    td('Significant issue — service degraded but not fully down')],
        [td('CRITICAL'),   tdc('8,003'),   tdc('2.70 %'),    td('Severe failure — immediate engineer action required')],
        [td('MINOR'),      tdc('1,369'),   tdc('0.46 %'),    td('Small issue — usually self-resolves without action')],
        [td('WARNING'),    tdc('1,018'),   tdc('0.34 %'),    td('Early indicator — watch closely for escalation')],
        [td('<b>TOTAL</b>'),tdc('<b>296,000</b>'), tdc('<b>100 %</b>'), td('All alarm records in the dataset')],
    ]
    story.append(Table(sev, colWidths=[BODY_W*0.16, BODY_W*0.16, BODY_W*0.16, BODY_W*0.52], style=tbl_style()))
    story.append(caption('Table 5.2 — Alarm Severity Distribution'))
    story.append(sp(6))

    pie1 = pie_chart(
        ['CLEARED 92.46%','MAJOR 4.03%','CRITICAL 2.70%','MINOR 0.46%','WARNING 0.34%'],
        [273684, 11926, 8003, 1369, 1018],
        'Alarm Severity Distribution (296,000 Total)',
        ['#2e7d32','#e65100','#c62828','#1565c0','#7b1fa2']
    )
    story.append(Table([[pie1]], colWidths=[BODY_W],
        style=TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),
                          ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)])))
    story.append(caption('Figure 5.1 — Pie Chart: Alarm Severity Distribution'))
    story.append(sp(6))

    # 5.2 Top conditions
    story.append(PageBreak())
    story += subsection('5.2  Top Alarm Conditions by Frequency')
    story.append(bp('Out of 63 unique alarm types, just 3 conditions account for nearly 90% of all alarms.'))
    story.append(bp('Fixing the top 3 alarm sources would eliminate most of the noise in the system.'))
    story.append(sp(6))

    cond = [
        [th('Alarm Condition'),                  th('Count'),   th('% of Total'), th('Clear Rate'), th('Category')],
        [td('BGP-5-ADJCHANGE_DOWN'),             tdc('159,679'),tdc('53.95 %'),   tdc('94.4 %'),    td('BGP Routing')],
        [td('LINK_DOWN'),                        tdc('60,400'), tdc('20.41 %'),   tdc('88.3 %'),    td('Physical Layer')],
        [td('TV_CE_PERFORMANCE_INTERFACE'),      tdc('45,864'), tdc('15.49 %'),   tdc('98.0 %'),    td('Carrier Ethernet')],
        [td('L2-L2VPN_PW-3-UPDOWN'),            tdc('8,645'),  tdc('2.92 %'),    tdc('94.3 %'),    td('L2VPN')],
        [td('Optical Alarm'),                    tdc('7,595'),  tdc('2.57 %'),    tdc('88.9 %'),    td('Optical / Fibre')],
        [td('ROUTING-ISIS-5-ADJCHANGE_DOWN'),    tdc('2,451'),  tdc('0.83 %'),    td('—'),           td('IS-IS Routing')],
        [td('SECURITY-SSHD-4-INFO_FAILURE'),     tdc('1,313'),  tdc('0.44 %'),    td('—'),           td('Security')],
        [td('ROUTING-OSPF-5-ADJCHG'),            tdc('580'),    tdc('0.20 %'),    td('—'),           td('OSPF Routing')],
    ]
    cw_cond = [BODY_W*0.36, BODY_W*0.12, BODY_W*0.13, BODY_W*0.12, BODY_W*0.27]
    story.append(Table(cond, colWidths=cw_cond, style=tbl_style()))
    story.append(caption('Table 5.3 — Top Alarm Conditions by Frequency'))
    story.append(sp(6))

    bar1 = hbar_chart(
        ['BGP-5-ADJCHANGE_DOWN','LINK_DOWN','TV_CE_PERFORMANCE',
         'L2-L2VPN_PW-3-UPDOWN','Optical Alarm','ISIS_ADJCHANGE','SSHD_FAILURE','OSPF_ADJCHG'],
        [159679,60400,45864,8645,7595,2451,1313,580],
        'Top Alarm Conditions by Frequency'
    )
    story.append(Table([[bar1]], colWidths=[BODY_W],
        style=TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),
                          ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)])))
    story.append(caption('Figure 5.2 — Bar Chart: Alarm Conditions by Count'))
    story.append(sp(6))

    # 5.3 Category breakdown
    story += subsection('5.3  Alarm Category Breakdown')
    cat = [
        [th('Category'),              th('Count'),   th('% of Total')],
        [td('BGP Routing'),           tdc('159,679'),tdc('53.95 %')],
        [td('Switches & Routers'),    tdc('62,604'), tdc('21.15 %')],
        [td('Carrier Ethernet (CE)'), tdc('45,865'), tdc('15.49 %')],
        [td('CE VPN'),                tdc('13,018'), tdc('4.40 %')],
        [td('Optical Transport'),     tdc('7,637'),  tdc('2.58 %')],
        [td('IS-IS Routing'),         tdc('2,451'),  tdc('0.83 %')],
        [td('Security'),              tdc('1,313'),  tdc('0.44 %')],
        [td('System'),                tdc('1,335'),  tdc('0.45 %')],
        [td('SONET / SDH'),           tdc('1,263'),  tdc('0.43 %')],
        [td('OSPF Routing'),          tdc('580'),    tdc('0.20 %')],
        [td('MPLS'),                  tdc('255'),    tdc('0.09 %')],
    ]
    story.append(Table(cat, colWidths=[BODY_W*0.50, BODY_W*0.25, BODY_W*0.25], style=tbl_style()))
    story.append(caption('Table 5.4 — Alarm Category Breakdown'))
    story.append(sp(6))

    pie2 = pie_chart(
        ['BGP','Switches & Routers','CE','CE VPN','Optical','IS-IS','Others'],
        [159679,62604,45865,13018,7637,2451,4746],
        'Alarm Category Share (%)',
        ['#c62828','#1565c0','#2e7d32','#e65100','#7b1fa2','#006064','#78909c']
    )
    story.append(Table([[pie2]], colWidths=[BODY_W],
        style=TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),
                          ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)])))
    story.append(caption('Figure 5.3 — Pie Chart: Alarm Category Distribution'))
    story.append(sp(6))

    # 5.4 Device concentration
    story.append(PageBreak())
    story += subsection('5.4  Device / Node Alarm Concentration')
    story.append(bp('Alarm generation was extremely concentrated — just 2 devices caused 37.3% of ALL alarms.'))
    story.append(bp('This is a classic 80/20 rule: a small number of nodes cause the majority of the problems.'))
    story.append(bp('The median device generated only 30 alarms in 2.5 years — less than one alarm per month.'))
    story.append(sp(6))

    dev_stat = [
        [th('Metric'),                       th('Value'),       th('Interpretation')],
        [td('Total Unique Devices'),          tdc('572'),        td('Total network nodes being monitored')],
        [td('Mean Alarms per Device'),        tdc('517.5'),      td('Average alarm count across all 572 devices')],
        [td('Median Alarms per Device'),      tdc('30.5'),       td('Typical device had only ~30 alarms over 2.5 years')],
        [td('Standard Deviation'),            tdc('3,707.2'),    td('Extremely high — distribution is very unequal')],
        [td('Maximum — host-er2-router-03'),  tdc('81,798'),     td('Single device with 27.6% of all 296,000 alarms')],
        [td('Devices with fewer than 10 alarms'), tdc('170 (29.7%)'), td('Nearly a third of nodes are practically silent')],
        [td('Devices with 1,000+ alarms'),    tdc('52 (9.1%)'),  td('Only 9% of nodes are chronically high-alarm sources')],
    ]
    story.append(Table(dev_stat, colWidths=[BODY_W*0.36, BODY_W*0.19, BODY_W*0.45], style=tbl_style()))
    story.append(caption('Table 5.5 — Device Alarm Concentration Statistics'))
    story.append(sp(6))

    top_dev = [
        [th('Device Name'),          th('Total Alarms'), th('% of Total'), th('CRITICAL'), th('MAJOR'), th('CLEARED')],
        [td('host-er2-router-03'),   tdc('81,798'),      tdc('27.6 %'),    tdc('104'),     tdc('3,752'),tdc('77,816')],
        [td('host-er2-router-02'),   tdc('28,535'),      tdc('9.6 %'),     tdc('71'),      tdc('1,591'),tdc('26,773')],
        [td('host-er2-router-04'),   tdc('8,809'),       tdc('3.0 %'),     tdc('16'),      tdc('573'),  tdc('8,212')],
    ]
    cw_td = [BODY_W*0.32,BODY_W*0.15,BODY_W*0.13,BODY_W*0.12,BODY_W*0.13,BODY_W*0.15]
    story.append(Table(top_dev, colWidths=cw_td, style=tbl_style(hbg=RED)))
    story.append(caption('Table 5.6 — Top 3 Offender Devices'))
    story.append(sp(4))
    story += note('Just 2 devices (er2-router-03 + er2-router-02) generated 37.3% of ALL 296,000 alarms. '
                  'Targeted maintenance on these two nodes alone would dramatically reduce total alarm volume.')

    # 5.5 Temporal
    story += subsection('5.5  Temporal Analysis — The May 2026 Network Event')
    story.append(bp('93.8% of all 296,000 alarms were generated in May 2026 alone.'))
    story.append(bp('This points to a single massive network event, not a gradual everyday pattern.'))
    story.append(bp('Peak: 104,149 alarms in a single day (May 25, 2026).'))
    story.append(sp(6))

    temp = [
        [th('Period'),              th('Alarm Count'),       th('Notable Observation')],
        [td('Jan–Nov 2024'),        tdc('< 100 total'),      td('Near-zero — monitoring was not yet fully deployed')],
        [td('Dec 2024'),            tdc('82'),               td('First notable monitoring activity recorded')],
        [td('Jan–Jun 2025'),        tdc('~26 to 153'),       td('Low baseline — limited monitoring coverage')],
        [td('Nov 2025'),            tdc('636'),              td('Monitoring expansion begins')],
        [td('Jan 2026'),            tdc('6,389'),            td('Significant increase in monitored nodes')],
        [td('May 2026'),            tdc('277,791'),          td('+7,158.7% increase vs April — massive network event!')],
        [td('Peak: May 25, 2026'),  tdc('104,149 in one day'),td('Single busiest day in the entire 875-day dataset')],
        [td('Peak hour: 07:00 UTC'),tdc('20,001 alarms'),   td('Equals 12:30 IST — peak Indian business hours')],
    ]
    story.append(Table(temp, colWidths=[BODY_W*0.28, BODY_W*0.22, BODY_W*0.50], style=tbl_style()))
    story.append(caption('Table 5.7 — Monthly Alarm Volume Timeline'))
    story.append(sp(6))

    tl = timeline_chart(
        ['2024 Q1-Q3','Dec 2024','Jan-Jun 2025','Nov 2025','Jan 2026','Apr 2026','May 2026'],
        [50, 82, 100, 636, 6389, 3827, 277791],
        'Monthly Alarm Volume — The May 2026 Network Event'
    )
    story.append(Table([[tl]], colWidths=[BODY_W],
        style=TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),
                          ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)])))
    story.append(caption('Figure 5.4 — Timeline: Monthly Alarm Volume'))
    story.append(sp(6))

    # 5.6 Burst
    story.append(PageBreak())
    story += subsection('5.6  Burst Analysis — How Alarms Actually Arrive')
    story.append(bp('Alarms do NOT arrive evenly — they come in massive bursts (hundreds per second).'))
    story.append(bp('Normal monitoring systems fail during burst storms because they cannot process fast enough.'))
    story.append(bp('AlarmIQ uses Apache Kafka to handle bursts of up to 1,213 alarms in 58.5 seconds.'))
    story.append(sp(6))

    burst = [
        [th('Metric'),                     th('Value'),                        th('What It Means')],
        [td('Mean Inter-Alarm Interval'),   tdc('1,507.97 sec (25.1 min)'),    td('Misleading — distorted by long quiet gaps between burst events')],
        [td('Median Inter-Alarm Interval'), tdc('0.25 sec'),                   td('The TRUE typical gap — nearly zero. Bursts dominate completely.')],
        [td('Standard Deviation'),          tdc('59,117.5 sec (16.4 hrs)'),    td('Extreme variability — quiet gaps vs rapid burst storms')],
        [td('Intervals under 1 second'),    tdc('29,423  (58.8 % of gaps)'),   td('More than half of all alarms arrive in rapid-fire bursts')],
        [td('Intervals under 60 seconds'),  tdc('42,931  (85.9 %)'),           td('85.9% of all alarms are part of a burst event')],
        [td('Total Burst Events'),          tdc('4,406'),                      td('Distinct burst storm events detected in the dataset')],
        [td('Mean Burst Size'),             tdc('64 alarms per burst'),        td('On average, each burst storm contains 64 alarms')],
        [td('Maximum Single Burst'),        tdc('1,213 alarms in 58.5 sec'),   td('May 26, 2026 at 13:17 UTC — the largest single burst recorded')],
        [td('Alarms Inside Bursts'),        tdc('~95.3 % of all alarms'),      td('Almost all alarms in the dataset arrived during burst events')],
    ]
    story.append(Table(burst, colWidths=[BODY_W*0.30, BODY_W*0.28, BODY_W*0.42], style=tbl_style()))
    story.append(caption('Table 5.8 — Alarm Burst Behavior Statistics'))
    story.append(sp(6))

    # 5.7 Co-occurrence
    story += subsection('5.7  Causal Chain Analysis — How Failures Cascade')
    story.append(bp('When one alarm fires, related alarms often follow within minutes on the same device.'))
    story.append(bp('Studying these pairs reveals the cascade pattern: one root cause → many secondary alarms.'))
    story.append(bp('BGP failure reliably triggers L2VPN pseudowire failure within 5 minutes — 14,861 times confirmed.'))
    story.append(sp(6))

    cooc = [
        [th('Alarm Pair (Same Device, 5-Min Window)'),       th('Count'),  th('What It Means')],
        [td('BGP_DOWN + PW-3-UPDOWN (Pseudowire)'),          tdc('14,861'),td('BGP session loss triggers L2VPN pseudowire failure — predictable cascade')],
        [td('BGP_DOWN + Optical Alarm'),                     tdc('12,050'),td('Physical fibre fault propagating upward to the BGP routing layer')],
        [td('LINK_DOWN + CE_PERFORMANCE'),                   tdc('9,736'), td('Link failure degrading Carrier Ethernet (CE) throughput')],
        [td('BGP_DOWN + ISIS_DOWN'),                         tdc('7,887'), td('Full control plane collapse — both routing protocols fail together')],
        [td('BGP_DOWN + SATELLITE_PROBLEM'),                 tdc('7,276'), td('BGP session loss disrupting satellite chassis connectivity')],
        [td('BGP_DOWN + SSH_FAILURE'),                       tdc('2,449'), td('Management plane access lost simultaneously with routing failure')],
        [td('LINK_DOWN + cefcFRURemoved'),                   tdc('2,096'), td('Hardware module removal directly causing a link failure alarm')],
        [td('BGP_DOWN + LDP_NBR_CHANGE'),                    tdc('751'),   td('BGP and MPLS LDP (Label Distribution Protocol) collapsing together')],
    ]
    story.append(Table(cooc, colWidths=[BODY_W*0.48, BODY_W*0.12, BODY_W*0.40], style=tbl_style()))
    story.append(caption('Table 5.9 — Alarm Co-occurrence (Same Device, 5-Minute Window)'))
    story.append(sp(4))
    story += note('The BGP + Pseudowire pair (14,861 events) is the most important finding. '
                  'Implementing BGP-aware suppression could eliminate ~14,861 redundant '
                  'PW-3-UPDOWN alarms whenever BGP is already confirmed DOWN on that device.')

    # 5.8 Critical breakdown
    story.append(PageBreak())
    story += subsection('5.8  CRITICAL Alarm Breakdown (8,003 Total)')
    story.append(bp('LINK_DOWN is the dominant CRITICAL alarm — responsible for 87.5% of all critical events.'))
    story.append(bp('POWER_SUPPLY_FAILURE and FAN_FAILURE are rare but MUST never be suppressed — hardware risk.'))
    story.append(sp(6))

    crit = [
        [th('Condition'),                         th('Critical Count'), th('% of All Criticals')],
        [td('LINK_DOWN'),                         tdc('7,001'),         tdc('87.5 %')],
        [td('Optical Alarm'),                     tdc('448'),           tdc('5.6 %')],
        [td('IOSXE_RP_ALARM_INFO_LINK_DOWN'),     tdc('169'),           tdc('2.1 %')],
        [td('CONTROLLER-5-UPDOWN'),               tdc('154'),           tdc('1.9 %')],
        [td('XCVR_MISSING_LINK_DOWN'),            tdc('147'),           tdc('1.8 %')],
        [td('FAN_FAILURE'),                       tdc('29'),            tdc('0.4 %')],
        [td('SYSTEM_SHUTDOWN'),                   tdc('11'),            tdc('0.1 %')],
        [td('POWER_SUPPLY_FAILURE'),              tdc('9'),             tdc('0.1 %')],
    ]
    story.append(Table(crit, colWidths=[BODY_W*0.56, BODY_W*0.22, BODY_W*0.22], style=tbl_style(hbg=RED)))
    story.append(caption('Table 5.10 — CRITICAL Alarm Breakdown'))
    story.append(sp(6))

    pie3 = pie_chart(
        ['LINK_DOWN 87.5%','Optical 5.6%','RP_ALARM 2.1%','CTRL 1.9%','XCVR 1.8%','Others 1.1%'],
        [7001,448,169,154,147,84],
        'CRITICAL Alarms by Condition (8,003 Total)',
        ['#c62828','#e65100','#1565c0','#2e7d32','#7b1fa2','#78909c']
    )
    story.append(Table([[pie3]], colWidths=[BODY_W],
        style=TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),
                          ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)])))
    story.append(caption('Figure 5.5 — Pie Chart: CRITICAL Alarm Conditions'))

    # ══════════════════════════════════════════════════════
    # 6. MATHEMATICS
    # ══════════════════════════════════════════════════════
    story.append(PageBreak())
    story += section_hdr('6', 'Mathematical Analysis — Statistics Behind the Data')
    story.append(bp('Data science is built on mathematics. This section explains the key statistical concepts used in the project.'))
    story.append(bp('All formulas use real numbers from the dataset — nothing is abstract.'))
    story.append(sp(6))

    # 6.1 Mean Median Mode
    story += subsection('6.1  Mean, Median, and Mode — Measuring the Centre of Data')
    story.append(bp('<b>Mean</b> — the average. Add up all values and divide by how many there are.'))
    story.append(bp('<b>Median</b> — the middle value when all data is sorted. Less affected by extreme outliers.'))
    story.append(bp('<b>Mode</b> — the most frequently occurring value in the dataset.'))
    story.append(sp(4))

    story.append(Paragraph('<b>MEAN Formula:</b>', ST_SUBSEC))
    story.append(eq('Mean ( x\u0305 ) = ( x\u2081 + x\u2082 + ... + x\u2099 ) / n'))
    story.append(eq('x\u0305 = \u03a3 x\u1d62 / n'))

    mean_ex = [
        [th('Calculation'),                                         th('Value')],
        [td('Total alarms in dataset'),                             tdc('296,000')],
        [td('Number of days (n)'),                                  tdc('875')],
        [td('Mean alarms per day = 296,000 / 875'),                 tdc('338.3 alarms/day')],
        [td('Total alarms across all devices'),                     tdc('296,000')],
        [td('Number of devices (n)'),                               tdc('572')],
        [td('Mean alarms per device = 296,000 / 572'),              tdc('517.5 alarms/device')],
    ]
    story.append(Table(mean_ex, colWidths=[BODY_W*0.65, BODY_W*0.35], style=tbl_style()))
    story.append(caption('Table 6.1 — Mean Calculations on Real Dataset'))
    story.append(sp(6))

    story.append(Paragraph('<b>MEDIAN Formula:</b>', ST_SUBSEC))
    story.append(eq('For odd n:   Median = x [ (n+1)/2 ]'))
    story.append(eq('For even n:  Median = ( x[n/2] + x[n/2 + 1] ) / 2'))

    med_ex = [
        [th('Variable'),               th('Mean'),          th('Median'),   th('Why They Differ')],
        [td('Alarms per device'),       tdc('517.5'),        tdc('30.5'),    td('er2-router-03 with 81,798 alarms pulls the mean far above the typical device')],
        [td('Inter-alarm gap (sec)'),   tdc('1,507.97 sec'), tdc('0.25 sec'),td('Long quiet gaps between bursts inflate the mean — median reveals the true burst behavior')],
    ]
    story.append(Table(med_ex, colWidths=[BODY_W*0.22, BODY_W*0.18, BODY_W*0.14, BODY_W*0.46], style=tbl_style()))
    story.append(caption('Table 6.2 — Mean vs Median Comparison'))
    story.append(sp(4))
    story += note('When mean and median differ greatly, extreme outliers are present. '
                  'The median of 0.25 sec between alarms shows the TRUE behavior — burst storms, not steady flow.')

    story.append(Paragraph('<b>MODE:</b>', ST_SUBSEC))
    story.append(eq('Mode = argmax ( frequency ( x\u1d62 ) )'))
    story.append(bp('In this dataset: <b>Mode = BGP-5-ADJCHANGE_DOWN</b> with 159,679 occurrences (53.95% of all alarms).'))
    story.append(sp(6))

    # 6.2 StdDev
    story += subsection('6.2  Standard Deviation and Variance')
    story.append(bp('<b>Variance</b> measures how far data values are spread from the mean.'))
    story.append(bp('<b>Standard Deviation</b> is the square root of variance — easier to interpret in original units.'))
    story.append(bp('High standard deviation here means alarm generation is extremely unequal across devices.'))
    story.append(sp(4))

    story.append(eq('Variance  (\u03c3\u00b2) = \u03a3 ( x\u1d62 - x\u0305 )\u00b2 / n'))
    story.append(eq('Std Dev   (\u03c3)   = \u221a [ \u03a3 ( x\u1d62 - x\u0305 )\u00b2 / n ]'))

    sd_tab = [
        [th('Variable'),                  th('Mean (x\u0305)'),   th('Std Dev (\u03c3)'),   th('Interpretation')],
        [td('Alarms per device'),          tdc('517.5'),     tdc('3,707.2'),    td('Extremely high — a few devices dominate all alarm volume')],
        [td('Inter-alarm interval (sec)'), tdc('1,507.97'),  tdc('59,117.5'),   td('Extreme — long quiet gaps vs rapid burst storms')],
        [td('Daily alarm count'),          tdc('338.3'),     td('Very high'),   td('May 2026 event (104,149 in one day) dominates the entire distribution')],
    ]
    story.append(Table(sd_tab, colWidths=[BODY_W*0.28,BODY_W*0.15,BODY_W*0.17,BODY_W*0.40], style=tbl_style()))
    story.append(caption('Table 6.3 — Standard Deviation on Key Variables'))
    story.append(sp(6))

    # 6.3 SNR
    story += subsection('6.3  Signal-to-Noise Ratio (SNR)')
    story.append(bp('SNR tells us how many alarms are real signals (need attention) vs noise (safe to ignore).'))
    story.append(bp('A low SNR means the system is flooded with noise — exactly BSNL\'s problem.'))
    story.append(sp(4))

    story.append(eq('SNR = Signal Alarms / Noise Alarms'))
    story.append(eq('SNR = Non-CLEARED / CLEARED'))
    story.append(eq('SNR = (296,000 - 273,684) / 273,684'))
    story.append(eq('SNR = 22,316 / 273,684  =  0.0816'))
    story.append(eq('Inverse SNR  =  273,684 / 22,316  \u2248  12.26  \u2248  1 : 13'))
    story.append(bp('Result: For every <b>1 actionable alarm</b>, there were <b>13 noise alarms</b>.'))
    story.append(sp(6))

    # 6.4 Percentages
    story += subsection('6.4  Key Percentage Calculations')
    story.append(eq('Percentage = ( Count / Total ) \u00d7 100'))
    story.append(sp(4))

    pct_tab = [
        [th('What We Are Measuring'),                    th('Formula'),                                    th('Result')],
        [td('CLEARED alarms share'),                     td('273,684 / 296,000 × 100'),                   tdc('92.46 %')],
        [td('CRITICAL alarms share'),                    td('8,003 / 296,000 × 100'),                     tdc('2.70 %')],
        [td('BGP alarms share'),                         td('159,679 / 296,000 × 100'),                   tdc('53.95 %')],
        [td('Alarms in May 2026'),                       td('277,791 / 296,000 × 100'),                   tdc('93.85 %')],
        [td('er2-router-03 share'),                      td('81,798 / 296,000 × 100'),                    tdc('27.63 %')],
        [td('Top 2 devices combined'),                   td('(81,798 + 28,535) / 296,000 × 100'),         tdc('37.27 %')],
        [td('Burst-interval alarms (under 60 sec)'),     td('42,931 / 49,964 × 100'),                     tdc('85.90 %')],
    ]
    story.append(Table(pct_tab, colWidths=[BODY_W*0.35, BODY_W*0.40, BODY_W*0.25], style=tbl_style()))
    story.append(caption('Table 6.4 — Key Percentage Calculations'))
    story.append(sp(6))

    # 6.5 node_failure_rate
    story.append(PageBreak())
    story += subsection('6.5  Engineered Feature: node_failure_rate')
    story.append(bp('A custom feature created in this project to measure how "dangerous" a network node is.'))
    story.append(bp('It calculates the proportion of CRITICAL alarms out of all alarms on a given node.'))
    story.append(bp('A node with a small number of alarms but many CRITICAL ones can be MORE dangerous than a noisy node.'))
    story.append(sp(4))

    story.append(eq('node_failure_rate = CRITICAL alarms on node / total alarms on node'))

    nfr = [
        [th('Device'),                   th('CRITICAL Alarms'), th('Total Alarms'), th('node_failure_rate')],
        [td('host-er2-router-03'),        tdc('104'),            tdc('81,798'),       td('104 / 81,798 = 0.0013  (0.13 %)')],
        [td('host-er2-router-02'),        tdc('71'),             tdc('28,535'),       td('71 / 28,535  = 0.0025  (0.25 %)')],
        [td('A quiet device (example)'),  tdc('5'),              tdc('10'),           td('5 / 10 = 0.50  (50 %) — far more dangerous despite fewer alarms!')],
    ]
    story.append(Table(nfr, colWidths=[BODY_W*0.30, BODY_W*0.18, BODY_W*0.18, BODY_W*0.34], style=tbl_style()))
    story.append(caption('Table 6.5 — node_failure_rate Calculation Examples'))
    story.append(sp(4))
    story += note('Alarm count alone does NOT indicate risk. A device with 50% of alarms being CRITICAL '
                  'is far more dangerous than er2-router-03 which has a 0.13% critical rate despite its huge volume.')

    # 6.6 avg daily
    story += subsection('6.6  Engineered Feature: node_avg_daily_alarms')
    story.append(bp('Measures how many alarms a node generates per day on average.'))
    story.append(bp('Identifies "chronic" alarm sources — nodes that consistently generate high alarm counts.'))
    story.append(sp(4))

    story.append(eq('node_avg_daily_alarms = total alarms on node / active days of node'))
    story.append(eq('Example: er2-router-03 = 81,798 / 875 = 93.5 alarms per day'))
    story.append(sp(6))

    # 6.7 MoM
    story += subsection('6.7  Month-over-Month (MoM) Growth Calculation')
    story.append(bp('MoM growth shows how dramatically alarm volume changed between months.'))
    story.append(sp(4))

    story.append(eq('MoM Growth (%) = [ (Current Month - Previous Month) / Previous Month ] \u00d7 100'))
    story.append(eq('May 2026 MoM = [ (277,791 - 3,827) / 3,827 ] \u00d7 100'))
    story.append(eq('= [ 273,964 / 3,827 ] \u00d7 100  \u2248  7,158.7 %'))
    story.append(bp('A 7,158.7% month-over-month increase confirms a <b>catastrophic network event</b> in May 2026 — not a gradual monitoring expansion.'))
    story.append(sp(6))

    # ══════════════════════════════════════════════════════
    # 7. MARKOV CHAIN
    # ══════════════════════════════════════════════════════
    story.append(PageBreak())
    story += section_hdr('7', 'Markov Chain — Modelling Network State Transitions')
    story.append(bp('A <b>Markov Chain</b> is a mathematical model for a system that moves between states over time.'))
    story.append(bp('Key rule: the next state depends ONLY on the current state — not on past history.'))
    story.append(bp('This is called the <b>Memoryless Property</b> or the <b>Markov Property</b>.'))
    story.append(sp(4))

    story.append(eq('P ( S[t+1] = s\u2c7c  |  S[t] = s\u1d62 )  =  p\u1d62\u2c7c'))
    story.append(bp('Where: P = probability,  S[t] = current state,  S[t+1] = next state,  p\u1d62\u2c7c = transition probability from state i to state j.'))
    story.append(sp(6))

    # States
    story += subsection('7.1  Network Node States in the AlarmIQ Model')
    story.append(bp('Each BSNL network node can exist in one of four states based on its current alarm pattern.'))
    story.append(sp(4))

    states_t = [
        [th('State'),      th('Alarm Pattern Observed'),                              th('Meaning & Required Action')],
        [td('NORMAL'),     td('No alarms or only minor warnings'),                    td('Network operating correctly — no intervention needed')],
        [td('DEGRADED'),   td('MAJOR alarms, BGP session flapping, optical warnings'), td('Performance declining — monitor closely and prepare to act')],
        [td('CRITICAL'),   td('CRITICAL alarms, LINK_DOWN, hardware failure alerts'), td('Failure is imminent — dispatch an engineer immediately')],
        [td('DOWN'),       td('NODE_DOWN alarm — all services lost'),                  td('Complete outage — emergency response team required now')],
    ]
    story.append(Table(states_t, colWidths=[BODY_W*0.14, BODY_W*0.38, BODY_W*0.48], style=tbl_style()))
    story.append(caption('Table 7.1 — Network Node States for the Markov Chain Model'))
    story.append(sp(6))

    mk = markov_diagram()
    story.append(Table([[mk]], colWidths=[BODY_W],
        style=TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),
                          ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)])))
    story.append(caption('Figure 7.1 — Markov Chain: Network Node State Transition Diagram'))
    story.append(sp(6))

    # Transition matrix
    story += subsection('7.2  Transition Probability Matrix (P)')
    story.append(bp('The transition matrix shows the probability of moving from one state to another.'))
    story.append(bp('Each row must sum to 1.0 — the node always transitions to some state (including staying in the same state).'))
    story.append(sp(4))

    tpm = [
        [th('From \\ To'),  th('NORMAL'), th('DEGRADED'), th('CRITICAL'), th('DOWN'),  th('Row Sum')],
        [td('NORMAL'),      tdc('0.40'),  tdc('0.30'),    tdc('0.20'),    tdc('0.10'), tdc('1.00')],
        [td('DEGRADED'),    tdc('0.30'),  tdc('0.20'),    tdc('0.40'),    tdc('0.10'), tdc('1.00')],
        [td('CRITICAL'),    tdc('0.10'),  tdc('0.10'),    tdc('0.20'),    tdc('0.60'), tdc('1.00')],
        [td('DOWN'),        tdc('0.50'),  tdc('0.20'),    tdc('0.20'),    tdc('0.10'), tdc('1.00')],
    ]
    cw_tpm = [BODY_W*0.18,BODY_W*0.14,BODY_W*0.15,BODY_W*0.15,BODY_W*0.14,BODY_W*0.14]

    tpm_style = tbl_style()
    tpm_style.add('BACKGROUND',(0,1),(0,-1),NAVY)
    tpm_style.add('TEXTCOLOR',(0,1),(0,-1),WHITE)
    tpm_style.add('FONTNAME',(0,1),(0,-1),'Helvetica-Bold')
    tpm_style.add('BACKGROUND',(4,1),(4,-1),colors.HexColor('#fff3e0'))
    tpm_style.add('TEXTCOLOR',(4,1),(4,-1),colors.HexColor('#bf360c'))
    story.append(Table(tpm, colWidths=cw_tpm, style=tpm_style))
    story.append(caption('Table 7.2 — Transition Probability Matrix P (estimated from domain knowledge)'))
    story.append(sp(4))

    story.append(eq('P =  | 0.40  0.30  0.20  0.10 |'))
    story.append(eq('     | 0.30  0.20  0.40  0.10 |'))
    story.append(eq('     | 0.10  0.10  0.20  0.60 |'))
    story.append(eq('     | 0.50  0.20  0.20  0.10 |'))
    story.append(sp(6))

    # Sample question
    story.append(PageBreak())
    story += subsection('7.3  Sample Question and Full Solution')

    story.append(Table([[Paragraph(
        '<b>Question:</b> A BSNL network node is currently in the DEGRADED state. '
        'What is the probability that it will be in the CRITICAL state exactly two time steps later? '
        'Use the transition probability matrix P given in Table 7.2.',
        S('Q', fontSize=9.5, textColor=NAVY, fontName='Helvetica', leading=14))]],
        colWidths=[BODY_W],
        style=TableStyle([
            ('BACKGROUND',(0,0),(-1,-1), colors.HexColor('#e3f2fd')),
            ('BOX',(0,0),(-1,-1),1.2,BLUE),
            ('TOPPADDING',(0,0),(-1,-1),10),('BOTTOMPADDING',(0,0),(-1,-1),10),
            ('LEFTPADDING',(0,0),(-1,-1),12),('RIGHTPADDING',(0,0),(-1,-1),12),
        ])))
    story.append(sp(8))

    story.append(Paragraph('<b>Step-by-Step Solution:</b>', ST_SUBSEC))

    story.append(bp('<b>Step 1 — Identify the starting state vector.</b>'))
    story.append(sbp('The node is DEGRADED. Represent as a row vector:'))
    story.append(eq('\u03c0\u2080 = [ 0,  1,  0,  0 ]   (positions: NORMAL, DEGRADED, CRITICAL, DOWN)'))

    story.append(bp('<b>Step 2 — Understand what we need to find.</b>'))
    story.append(sbp('We need P\u00b2 (matrix P multiplied by itself) — the two-step transition probabilities.'))
    story.append(sbp('Specifically, we need element P\u00b2 [ DEGRADED \u2192 CRITICAL ].'))
    story.append(eq('P\u00b2 = P \u00d7 P'))

    story.append(bp('<b>Step 3 — Calculate P\u00b2 [ DEGRADED \u2192 CRITICAL ] by expanding over all intermediate states.</b>'))
    story.append(eq('P\u00b2 [ D \u2192 C ]  =  \u03a3  P [ D \u2192 k ]  \u00d7  P [ k \u2192 C ]   for k \u2208 { N, D, C, DOWN }'))
    story.append(eq('=  P[D\u2192N] \u00d7 P[N\u2192C]  +  P[D\u2192D] \u00d7 P[D\u2192C]  +  P[D\u2192C] \u00d7 P[C\u2192C]  +  P[D\u2192Down] \u00d7 P[Down\u2192C]'))

    story.append(bp('<b>Step 4 — Substitute values from the matrix.</b>'))
    story.append(eq('=  (0.30 \u00d7 0.20)  +  (0.20 \u00d7 0.40)  +  (0.40 \u00d7 0.20)  +  (0.10 \u00d7 0.20)'))
    story.append(eq('=   0.06   +   0.08   +   0.08   +   0.02'))
    story.append(eq('=   0.24'))

    story.append(sp(6))
    story.append(Table([[Paragraph(
        '\u2714  Answer: The probability that the node will be in the CRITICAL state '
        'two time steps later, given it is currently in DEGRADED state, is  0.24  (24%).',
        S('A', fontSize=11, textColor=WHITE, fontName='Helvetica-Bold', leading=16,
          alignment=TA_CENTER))]],
        colWidths=[BODY_W],
        style=TableStyle([
            ('BACKGROUND',(0,0),(-1,-1),NAVY),
            ('BOX',(0,0),(-1,-1),2,CYAN),
            ('TOPPADDING',(0,0),(-1,-1),12),('BOTTOMPADDING',(0,0),(-1,-1),12),
            ('LEFTPADDING',(0,0),(-1,-1),14),('RIGHTPADDING',(0,0),(-1,-1),14),
        ])))
    story.append(sp(8))

    story.append(Paragraph('<b>What this means in the real BSNL context:</b>', ST_SUBSEC))
    story.append(bp('If a node is currently showing DEGRADED behavior (BGP flapping, major alarms), there is a 24% chance it will reach CRITICAL failure within two monitoring cycles.'))
    story.append(bp('AlarmIQ is designed to detect this DEGRADED state and fire an early warning — giving engineers time to act before the node reaches DOWN.'))
    story.append(bp('This is exactly why predictive intelligence matters: react at DEGRADED, not after DOWN.'))
    story.append(sp(6))

    story += subsection('7.4  Steady-State Distribution (Bonus Concept)')
    story.append(bp('In the long run, a Markov Chain reaches a steady state where probabilities stabilise.'))
    story.append(bp('The steady-state vector \u03c0 satisfies:'))
    story.append(eq('\u03c0 \u00d7 P = \u03c0     and     \u03a3 \u03c0\u1d62 = 1'))
    story.append(bp('For AlarmIQ, this would tell BSNL what percentage of time nodes spend in each state long-term.'))
    story.append(bp('This is useful for planning NOC staffing levels and setting realistic SLA (Service Level Agreement) targets.'))

    # ══════════════════════════════════════════════════════
    # 8. ALL 22 STAGES
    # ══════════════════════════════════════════════════════
    story.append(PageBreak())
    story += section_hdr('8', 'The Complete Data Science Pipeline — All 22 Stages')
    story.append(bp('Every data science project follows a structured pipeline from problem definition to deployment.'))
    story.append(bp('All 22 stages below were completed during this internship at BSNL.'))
    story.append(sp(6))

    stages = [
        ('1',  'Problem Understanding',
         '• Translated NOC engineer pain points into a formal data science problem.\n'
         '• Identified: 296,000 alarms with 1:13 signal-to-noise ratio needed intelligent filtering.\n'
         '• Key constraint: data from Cisco EPNM — structured but inconsistent across vendors.'),
        ('2',  'Business Understanding',
         '• Identified 4 stakeholders: NOC Engineers, Network Planning, Enterprise Customers, Management.\n'
         '• Success metric defined: AlarmIQ fires an early warning BEFORE NODE_DOWN, not after.\n'
         '• Business goal: reduce MTTD (Mean Time To Detect) outages from minutes to seconds.'),
        ('3',  'Data Collection',
         '• Collected 296,000 alarm records from Cisco EPNM covering 875 days.\n'
         '• Dataset covers 572 unique network nodes and 63 distinct alarm condition types.\n'
         '• Format: structured tabular alarm dump from BSNL\'s monitoring system.'),
        ('4',  'Data Understanding',
         '• Discovered 93.8% of alarms are from May 2026 alone — one massive network event.\n'
         '• Found 92.46% of alarms are CLEARED — only ~7.5% needed any action.\n'
         '• Critical finding: 0 out of 296,000 alarms were ever acknowledged — full alarm fatigue.'),
        ('5',  'Data Quality Assessment',
         '• 149,388 BGP records mislabelled — BGP UP events recorded with DOWN condition name.\n'
         '• SSH success events logged as failure — contaminating ssh_failure feature.\n'
         '• Misspelled alarm name: "entsensor_CelsiusTemrouterrature_major" found (1 record).\n'
         '• Device name duplicates: same device split across two identities due to IP prefix differences.'),
        ('6',  'Data Cleaning & Preprocessing',
         '• Step 1: Removed duplicate alarm floods from the same logical event.\n'
         '• Step 2: Standardised all timestamps to UTC format.\n'
         '• Step 3: Mapped vendor-specific severity strings to standard levels.\n'
         '• Step 4: Unified alarm condition names (LINK_FAIL / LINK_FAILURE → standard name).\n'
         '• Step 5: Normalised device names — stripped IP prefixes to merge split identities.\n'
         '• Step 6: Reclassified 149,388 mislabelled BGP records using message content field.\n'
         '• Step 7: Removed records with missing timestamps, null device IDs, or empty conditions.'),
        ('7',  'Exploratory Data Analysis (EDA)',
         '• Top 3 conditions (BGP 53.9%, LINK_DOWN 20.4%, CE 15.5%) = 89.8% of all alarms.\n'
         '• Median inter-alarm interval = 0.25 seconds — confirms burst-driven behavior.\n'
         '• Just 2 devices (er2-router-03 + er2-router-02) = 37.3% of all 296,000 alarms.\n'
         '• Peak alarm hour: 07:00 UTC (20,001 alarms) = 12:30 IST — peak Indian business hours.'),
        ('8',  'Feature Engineering',
         '• node_failure_rate: CRITICAL alarms / total alarms per node.\n'
         '• node_avg_daily_alarms: average alarms generated per day per node.\n'
         '• alarm_burst_density: number of alarms in a 60-second sliding window.\n'
         '• bgp_down_count: count of BGP_DOWN alarms in the current window.\n'
         '• co_occurrence_score: count of co-occurring alarm pairs within 5 minutes.\n'
         '• optical_before_link: Boolean — did an optical alarm precede a LINK_DOWN?\n'
         '• hardware_alarm_flag: Boolean — is cefcFRURemoved or PowerStatusChange present?\n'
         '• sequence_similarity_score: similarity to learned pre-failure alarm sequences.'),
        ('9',  'Feature Selection',
         '• Removed: acknowledged alarm features (zero variance — 0 acks across full dataset).\n'
         '• Capped: minutes_since_last_critical at 120 min (raw 9,999 sentinel value distorted the model).\n'
         '• Minimum window rule: model requires at least 5 alarms before firing any warning.\n'
         '• Retained: node_failure_rate, burst_density, bgp_down_count, hardware_alarm_flag, sequence_similarity_score.'),
        ('10', 'Data Splitting',
         '• Used time-based split — NEVER random (random would leak future data into training).\n'
         '• Training: Jan 2024 – Mar 2026 (~18,000 records) — learn normal behavior patterns.\n'
         '• Validation: Apr 2026 (~3,827 records) — tune thresholds and hyperparameters.\n'
         '• Test: May 2026 (~277,791 records) — evaluate on the real catastrophic network event.'),
        ('11', 'Model Selection',
         '• Selected: Isolation Forest — an unsupervised anomaly detection algorithm.\n'
         '• Reason: no labelled outage examples existed — BSNL had no pre-tagged failure data.\n'
         '• Paired with: a Sequence Matching Engine for pre-failure pattern detection.\n'
         '• Supported by: a Node State Engine maintaining sliding windows per device.'),
        ('12', 'Model Training',
         '• Phase 1: Grouped all alarms chronologically by network node to build timelines.\n'
         '• Phase 2: Extracted alarm sequences that occurred before CRITICAL events.\n'
         '• Phase 3: Trained Isolation Forest on node-level feature vectors — learned normal baseline.\n'
         '• Phase 4: Populated Pattern Database with sequences, risk levels, and confidence scores.'),
        ('13', 'Hyperparameter Tuning',
         '• contamination = 0.075 (matches empirical 7.5% non-CLEARED rate).\n'
         '• n_estimators = 200 (balances accuracy vs realtime processing speed).\n'
         '• Minimum window size = 5 alarms (prevents false positives from 2-alarm windows).\n'
         '• Co-occurrence window = 5 minutes (matches empirical BGP → Pseudowire cascade timing).'),
        ('14', 'Model Evaluation',
         '• Correctly detected BGP instability → L2VPN cascade warnings before customer impact.\n'
         '• Correctly identified er2-router-03 as highest-risk node (27.6% of all alarms).\n'
         '• Hardware alarms (FAN_FAILURE, POWER_SUPPLY_FAILURE) correctly never suppressed.\n'
         '• 3 of 4 early false positives fixed by introducing the 5-alarm minimum window rule.'),
        ('15', 'Model Interpretation',
         '• Every warning includes a human-readable root cause explanation.\n'
         '• Example: "Environmental alarm on PE router = HARDWARE root cause. Routing alarms = secondary."\n'
         '• Root cause logic: LINK_DOWN preceded by optical alarm = fibre fault; isolated = SFP failure.\n'
         '• Top predictive feature: sequence_similarity_score — core anomaly detection driver.'),
        ('16', 'Business Insights Generation',
         '• Insight 1: Focus maintenance on er2-router-03 and er2-router-02 → eliminate 37.3% of alarms.\n'
         '• Insight 2: BGP-aware suppression would eliminate ~14,861 redundant PW-3-UPDOWN alarms.\n'
         '• Insight 3: Zero acknowledgements across 296,000 alarms = urgent need for an SLA mandate.\n'
         '• Insight 4: POWER_SUPPLY_FAILURE and FAN_FAILURE need a dedicated escalation workflow.\n'
         '• Insight 5: NOC staffing should be maximised during 06:00–09:00 UTC (11:30–14:30 IST).'),
        ('17', 'Deployment',
         '• Stream Ingestion: Apache Kafka — handles burst storms (1,213 alarms in 58.5 seconds).\n'
         '• Processing Engine: Python (PySpark / Kafka Consumer).\n'
         '• ML Model: Scikit-learn Isolation Forest.\n'
         '• Pattern Storage: PostgreSQL — stores learned sequences, node histories, risk scores.\n'
         '• Dashboard: Grafana / Custom Web UI for realtime visualisation.'),
        ('18', 'Dashboard & Visualisation',
         '• Panel 1: Live Alarm Feed — scrolling table filtered by severity (RED = CRITICAL).\n'
         '• Panel 2: Active Early Warnings — list of current warnings per node with risk level.\n'
         '• Panel 3: Node Risk Status Map — topology view with nodes colour-coded by risk level.\n'
         '• Panel 4: Incident Summaries — human-readable summaries replacing raw alarm floods.\n'
         '• Panel 5: Outage Trend Charts — daily/hourly alarm volume and burst frequency trends.'),
        ('19', 'Monitoring & Performance Tracking',
         '• Tracks false positive rate — are engineers ignoring warnings?\n'
         '• Tracks false negative rate — are outages happening without prior warning?\n'
         '• Monitors Kafka consumer lag — is the pipeline keeping up with burst storms?\n'
         '• Tracks pattern match rates — are learned sequences still relevant to current behavior?'),
        ('20', 'Model Retraining & Continuous Improvement',
         '• Retraining triggers: new alarm types, topology changes, pattern drift, scheduled review.\n'
         '• Future improvements: LSTM for sequence modelling, adaptive pattern learning.\n'
         '• Planned: topology-aware correlation, region-level outage intelligence.\n'
         '• Planned: distributed stream scaling for national BSNL deployment.'),
        ('21', 'Documentation',
         '• Technical Architecture Document: full system design with data flow diagrams.\n'
         '• Data Quality Report: all quality issues found and the fixes applied.\n'
         '• EDA Report: complete dataset analysis with charts and statistics.\n'
         '• Operations Manual: how NOC engineers use the dashboard and interpret warnings.'),
        ('22', 'Presentation & Communication',
         '• Processed 296,000 real BSNL alarm records from Cisco EPNM.\n'
         '• Corrected 149,388 mislabelled records — 51% of the entire dataset.\n'
         '• Engineered 10 domain-specific features combining network knowledge and ML.\n'
         '• Designed a production-ready architecture on Kafka + PostgreSQL + Grafana.\n'
         '• Delivered this report as the final internship placement deliverable.'),
    ]

    for num, name, desc in stages:
        # build bullet paragraphs for the description cell
        desc_paras = []
        for line in desc.split('\n'):
            line = line.strip()
            if line:
                desc_paras.append(Paragraph(line, S('sdesc', fontSize=8.5, textColor=DTEXT,
                                                     leading=12, fontName='Helvetica',
                                                     leftIndent=4)))
        stage_row = [
            [Paragraph(f'Stage<br/>{num}',
                       S('snum', fontSize=9, textColor=WHITE, fontName='Helvetica-Bold',
                         alignment=TA_CENTER, leading=13)),
             Paragraph(name,
                       S('sname', fontSize=9.5, textColor=NAVY, fontName='Helvetica-Bold',
                         leading=14)),
             desc_paras],
        ]
        story.append(Table(stage_row,
            colWidths=[BODY_W*0.09, BODY_W*0.22, BODY_W*0.69],
            style=TableStyle([
                ('BACKGROUND',   (0,0),(0,0), BLUE),
                ('BACKGROUND',   (1,0),(1,0), LGRAY),
                ('BACKGROUND',   (2,0),(2,0), WHITE),
                ('VALIGN',       (0,0),(-1,-1),'TOP'),
                ('GRID',         (0,0),(-1,-1), 0.4, MGRAY),
                ('TOPPADDING',   (0,0),(-1,-1), 5),
                ('BOTTOMPADDING',(0,0),(-1,-1), 5),
                ('LEFTPADDING',  (0,0),(-1,-1), 6),
                ('RIGHTPADDING', (0,0),(-1,-1), 6),
            ])))
        story.append(sp(2))

    # ══════════════════════════════════════════════════════
    # 9. SYSTEM ARCHITECTURE
    # ══════════════════════════════════════════════════════
    story.append(PageBreak())
    story += section_hdr('9', 'AlarmIQ System Architecture')
    story.append(bp('AlarmIQ has two layers: an Offline Learning Layer and a Realtime Stream Layer.'))
    story.append(bp('Both layers share the Pattern Database — offline learning feeds what the realtime layer uses.'))
    story.append(sp(6))

    story += subsection('9.1  Offline Learning Layer')
    off_steps = [
        [th('Step'), th('Component'),                                  th('What Happens')],
        [tdc('1'),   td('Historical Alarm Dumps\n(EPNM / OMCR / NMS)'),td('Raw alarm export files sourced from Cisco EPNM — the starting point for all learning')],
        [tdc('2'),   td('Cleaning & Normalisation'),                   td('Removes duplicates, fixes timestamps, standardises severity levels, unifies device names')],
        [tdc('3'),   td('Node Timeline Construction'),                  td('Groups all alarms chronologically per network node to create ordered event sequences')],
        [tdc('4'),   td('Pre-Failure Sequence Extraction'),             td('Extracts alarm sequences that occurred in the minutes before each CRITICAL event')],
        [tdc('5'),   td('Pattern Learning Engine\n(Isolation Forest)'), td('Learns the normal alarm baseline and identifies anomalous pre-failure patterns')],
        [tdc('6'),   td('Pattern Database (PostgreSQL)'),               td('Stores all learned sequences with risk levels, confidence scores, and historical frequencies')],
    ]
    story.append(Table(off_steps, colWidths=[BODY_W*0.07, BODY_W*0.30, BODY_W*0.63], style=tbl_style(hbg=TEAL)))
    story.append(caption('Table 9.1 — Offline Learning Layer Steps'))
    story.append(sp(8))

    story += subsection('9.2  Realtime Stream Layer')
    rt_steps = [
        [th('Step'), th('Component'),                             th('What Happens')],
        [tdc('1'),   td('Live Kafka Alarm Streams'),              td('Continuous alarm events arrive from all 572 nodes in real time — including burst storms')],
        [tdc('2'),   td('Raw Alarm Persistence'),                 td('All alarms saved before processing — enables replay, fault recovery, and audit')],
        [tdc('3'),   td('Kafka Consumer Layer'),                  td('Consumes alarms, parses them, buffers, and forwards for processing')],
        [tdc('4'),   td('Normalisation & Deduplication'),         td('Same cleaning rules as offline layer applied in real time — ensures consistent data quality')],
        [tdc('5'),   td('Realtime Node State Engine'),            td('Maintains a bounded sliding window of recent alarms per node — tracks current alarm sequence')],
        [tdc('6'),   td('Sequence Matching Engine'),              td('Compares the live alarm sequence against the Pattern Database for similarity')],
        [tdc('7'),   td('Risk Evaluation Layer'),                 td('Calculates Risk Level, Confidence Score, Severity, and Similarity to known patterns')],
        [tdc('8'),   td('Early Warning Generator'),               td('Fires a warning: "Node BTS_205 — high-risk pre-failure sequence detected"')],
        [tdc('9'),   td('Incident Summarisation'),                td('Converts the raw alarm flood into one human-readable summary for the NOC engineer')],
        [tdc('10'),  td('PostgreSQL + Dashboard (Grafana)'),      td('Stores warnings and provides the realtime dashboard for NOC visualisation')],
    ]
    story.append(Table(rt_steps, colWidths=[BODY_W*0.07, BODY_W*0.28, BODY_W*0.65], style=tbl_style(hbg=CYAN)))
    story.append(caption('Table 9.2 — Realtime Stream Layer Steps'))
    story.append(sp(8))

    story += subsection('9.3  Technology Stack')
    tech = [
        [th('Component'),        th('Technology'),                          th('Role in AlarmIQ')],
        [td('Stream Ingestion'), td('Apache Kafka'),                        td('Handles burst storms — up to 1,213 alarms in 58.5 sec — without losing any data')],
        [td('Processing Engine'),td('Python (PySpark / Kafka Consumer)'),   td('Cleans, normalises, and routes alarm events between pipeline stages')],
        [td('ML Model'),         td('Scikit-learn Isolation Forest'),        td('Unsupervised anomaly detection — identifies pre-failure alarm patterns')],
        [td('Pattern Storage'),  td('PostgreSQL'),                          td('Stores learned sequences, node histories, and real-time risk scores')],
        [td('Dashboard'),        td('Grafana / Custom Web UI'),             td('Realtime visualisation of alarms, early warnings, and node risk status')],
        [td('Persistence'),      td('PostgreSQL / Object Storage'),         td('Raw alarm archive for post-incident replay and historical analysis')],
    ]
    story.append(Table(tech, colWidths=[BODY_W*0.20, BODY_W*0.30, BODY_W*0.50], style=tbl_style()))
    story.append(caption('Table 9.3 — AlarmIQ Technology Stack'))

    # ══════════════════════════════════════════════════════
    # 10. RESULTS
    # ══════════════════════════════════════════════════════
    story.append(PageBreak())
    story += section_hdr('10', 'Results & Impact Summary')
    story.append(bp('AlarmIQ successfully transformed 296,000 raw alarm records into an intelligent early warning system.'))
    story.append(bp('The results below are all based on real BSNL network data processed during the internship.'))
    story.append(sp(6))

    results = [
        [th('Metric'),                           th('Result')],
        [td('Dataset Processed'),                td('296,000 alarm records across 875 days (Jan 2024 – May 2026)')],
        [td('Data Quality Issues Fixed'),        td('149,388 mislabelled records corrected — that is 51% of the entire dataset')],
        [td('Alarm Types Mapped'),               td('63 unique alarm condition types mapped across 11 categories')],
        [td('Network Nodes Analysed'),           td('572 individual nodes across BSNL\'s network infrastructure')],
        [td('Burst Events Detected'),            td('4,406 burst storm events identified and characterised')],
        [td('Cascade Patterns Found'),           td('10 major co-occurrence pairs mapped (e.g., BGP → Pseudowire: 14,861 events)')],
        [td('Noise Reduction'),                  td('92.46% of CLEARED alarms automatically deprioritised from engineer queues')],
        [td('False Positive Reduction'),         td('5-alarm minimum window rule eliminated 75% of early false positives')],
        [td('Business Insight 1'),               td('Focus on just 2 routers → eliminate 37.3% of total alarm volume')],
        [td('Business Insight 2'),               td('BGP-aware suppression → eliminate ~14,861 redundant PW-3-UPDOWN alarms')],
        [td('Architecture Delivered'),           td('Production-ready pipeline on Apache Kafka + PostgreSQL + Grafana')],
    ]
    story.append(Table(results, colWidths=[BODY_W*0.35, BODY_W*0.65], style=tbl_style(hbg=GREEN)))
    story.append(caption('Table 10.1 — AlarmIQ Project Results'))
    story.append(sp(6))
    story += highlight_box(
        '• From 296,000 raw alarms  \u2192  Actionable early warnings + Incident summaries.\n'
        '• From 1 : 13 signal-to-noise ratio  \u2192  Targeted CRITICAL-only alerting.\n'
        '• From 0 acknowledgements (full alarm fatigue)  \u2192  Prioritised warning queue for NOC engineers.')

    story += subsection('10.1  Skills Demonstrated During This Internship')
    skills = [
        [th('Skill Area'),              th('Specific Demonstration in This Project')],
        [td('Domain Knowledge'),        td('Telecom networks, MPLS, BGP, L3VPN, L2VPN, Cisco ASR 9000 router architecture')],
        [td('Data Quality'),            td('Identified and corrected 149,388 mislabelled records; built 7-step normalisation pipeline')],
        [td('EDA'),                     td('Time-series alarm analysis, burst detection, co-occurrence analysis on 296,000 records')],
        [td('Feature Engineering'),     td('10 domain-specific features combining telecom network knowledge with statistical ML features')],
        [td('Machine Learning'),        td('Unsupervised Isolation Forest anomaly detection with full hyperparameter tuning')],
        [td('Stream Processing'),       td('Apache Kafka architecture designed for real-time alarm ingestion at national scale')],
        [td('Visualisation'),           td('5-panel Grafana dashboard designed for NOC operational use')],
        [td('Business Insight'),        td('5 actionable business recommendations translated from technical data findings')],
        [td('System Architecture'),     td('End-to-end design: offline learning + realtime stream + dashboard stack')],
        [td('Mathematical Analysis'),   td('Descriptive statistics, SNR, node_failure_rate formula, Markov Chain modelling')],
    ]
    story.append(Table(skills, colWidths=[BODY_W*0.28, BODY_W*0.72], style=tbl_style()))
    story.append(caption('Table 10.2 — Skills Demonstrated'))

    # ══════════════════════════════════════════════════════
    # 11. CONCLUSION
    # ══════════════════════════════════════════════════════
    story.append(PageBreak())
    story += section_hdr('11', 'Conclusion')
    story.append(bp('AlarmIQ demonstrates the full power of data science applied to real national telecom infrastructure.'))
    story.append(bp('The raw alarm data was 92.46% noise — without intelligence, engineers are completely paralysed.'))
    story.append(bp('Just 2 network nodes generated 37.3% of all alarms — targeted maintenance here would have dramatic impact.'))
    story.append(bp('149,388 records (51%) were mislabelled — data quality work was as critical as the ML model itself.'))
    story.append(bp('Alarm cascades follow predictable patterns — BGP failure reliably causes L2VPN failure within 5 minutes.'))
    story.append(bp('Markov Chain modelling shows a 24% probability that a DEGRADED node reaches CRITICAL in two steps — enabling proactive intervention.'))
    story.append(bp('All 22 stages of the data science pipeline were completed — from problem understanding to deployment architecture.'))
    story.append(sp(10))
    story.append(bp('This internship at BSNL provided hands-on experience with real national-scale telecom data, combining deep network domain knowledge with modern data science and machine learning to solve a genuine operational problem affecting millions of Indian telecom users.'))
    story.append(sp(20))
    story.append(HRFlowable(width='100%', thickness=0.8, color=BLUE, spaceAfter=8))
    story.append(Paragraph(
        'AlarmIQ — Telecom Alarm Intelligence &amp; Early Warning System  |  '
        'BSNL Internship Report  |  ALESTOR ALDOUS  |  Model Engineering College',
        ST_CAP))

    doc.build(story)
    print(f'Done: {out}')

build()
PYEOF
echo "Script written"