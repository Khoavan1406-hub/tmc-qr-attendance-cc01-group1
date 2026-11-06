"""Figure 5.2 - Recovery order after reconnection (style v2, same conventions as Fig 4.16/4.17)."""
import sys; sys.path.insert(0,'.')
from seq2 import Seq
S=Seq([("gc","component","Gate Core"),("ssf","component","Site Sync (factory)"),
       ("ssc","component","Site Sync (central)"),("acc","component","Access &amp; Configuration"),
       ("att","component","Attendance"),("ar","component","Attendance Review")],
      spacing=166,head_w=158,head_h=66,fs=16,row=10,frame_hdr=27,first=24)
X=S.X; R=S.right-14
def phase(t):
    S.gap(4)
    S.v(f"<b>{t}</b>",S.left-6,S.y,S.right-S.left+12,24,"rounded=0;fillColor=#f2f2f2;strokeColor=none;align=left;spacingLeft=8;fontSize=15;fontColor=#444444;")
    S.gap(30)
# 1 eligibility
phase("Recheck eligibility data before Gate Core uses it again")
S.msg("ssf","ssc","applied watermark, versions","async")
S.msg("ssc","acc","getChanges(after watermark)","sync")
S.msg("acc","ssc","change sequence or full snapshot","reply")
S.msg("ssc","ssf","Access Change Feed: missing changes, checkpoint","async")
S.msg("ssf","gc","applyEligibilityChanges","sync",activate=True)
S.self_("gc","check versions, continuity, clock bound",w=330)
S.frame("alt","watermark ≤ 60 s and all conditions hold")
S.self_("gc","return to normal mode",w=230)
S.else_("watermark > 60 s, < 30 min, and the §5.7.2 conditions hold")
S.self_("gc","keep degraded mode",w=230,
        side=("new scans are flagged",X["ssc"]-20,R,1))
S.else_("else")
S.self_("gc","stop automatic acceptance",w=250,
        side=("new scans get CANNOT PROCESS",X["ssc"]-20,R,1))
S.end()
S.off("gc")
# 2 delivery
phase("Deliver stored events and wait for processing")
S.frame("loop","each pending outbox event, in order",x1=X["ssf"]-60,x2=S.right+8)
S.msg("ssf","att","Gate Event Feed: event (original eventId, version)","async",activate=True,
      note=("applied once per eventId",X["att"]+20,R,1),note_extra=False)
S.gap(14)
S.msg("att","ssf","processing acknowledgement","reply"); S.off("att")
S.self_("ssf","mark delivered",w=180,
        side=("audit events are resent the same way on their own channel",X["ssc"]+10,R,1))
S.end()
S.self_("ar","",w=0,side=("compare DEGRADED transactions with<br>applied changes; open review cases",X["ssc"]+20,X["ar"]-40,2))
# 3 resolutions
phase("Apply scan resolutions")
S.frame("opt","resolved case is linked to a scan that Gate Core must update")
S.msg("ar","gc","Resolution Feed: decision (decisionId)","async",activate=True)
S.self_("gc","store the matching decision",w=260)
S.msg("gc","ar","Gate Event Feed: ResolutionApplied(decisionId)","async")
S.off("gc")
S.notebox("applying the decision to Attendance is tracked separately (§4.2.3, B6–B8)",X["ssf"]-20,R,1)
S.end()
S.ll_end=S.y-6
open(sys.argv[1],'w').write(S.xml("Figure 5.2 Recovery after reconnection","Figure52Recovery"))
