"""Figure 4.17 - Payroll handover sequence (style v2, same conventions as Fig 4.16)."""
import sys; sys.path.insert(0,'.')
from seq2 import Seq
S=Seq([("tr","client","Scheduler or Back-office Portal"),("ph","component","Payroll Handover"),
       ("att","component","Attendance"),("ad","component","HR/Payroll Adapter"),
       ("hr","external system","HR/Payroll System")],
      spacing=196,head_w=184,head_h=66,fs=16,row=10,frame_hdr=27,first=28)
X=S.X; R=S.right-14
NR=X["ph"]+22
# 1 start
S.msg("tr","ph","startPeriod(period)","sync",step=1,activate=True)
# 2 collect
S.msg("ph","att","queryPeriod(period)","sync",step=2,activate=True)
S.msg("att","ph","data, version, progress","reply"); S.gap(8)
# 3 open cases
S.msg("ph","att","getOpenCases(period)","sync",step=3,
      note=("HR staff handle exceptions",X["att"]+20,R,1))
S.msg("att","ph","open cases, decisions","reply"); S.off("att"); S.gap(6)
# 4 prepare
S.self_("ph","prepare batch",step=4,w=160,
        side=("fix data version, assign batchId,<br>store batch content",X["att"]+30,R,2))
# 5 submit
S.msg("ph","ad","submit(batch, batchId)","sync",step=5,activate=True)
S.msg("ad","hr","send batch","sync")
S.frame("opt","response received",x1=S.X["ad"]-150,x2=S.right-4)
S.msg("hr","ad","ack | rejection","reply")
S.end()
S.notebox("timeout or insufficient receipt evidence → UNDETERMINED",S.X["ad"]-150,R,1)
S.msg("ad","ph","outcome","reply"); S.off("ad")
# 6 record
S.self_("ph","record outcome",step=6,w=160,
        side=("store outcome with audit evidence",X["att"]+30,R,1))
S.frame("alt","CONFIRMED")
S.notebox("HR/Payroll acknowledged the batch",NR,R,1)
S.else_("REJECTED")
S.notebox("an authorized user corrects the data; a new version is prepared",NR,R,1)
S.else_("UNDETERMINED")
S.frame("opt","the API supports a status query",x1=S.left+24,x2=S.right-4)
S.msg("ph","ad","queryStatus(batchId)","sync",step=7,activate=True)
S.msg("ad","ph","status","reply"); S.off("ad")
S.end()
S.frame("alt","outcome now known",x1=S.left+24,x2=S.right-4)
S.self_("ph","record outcome",w=160,
        side=("store with evidence; continue as CONFIRMED or REJECTED",X["att"]+30,R,1))
S.else_("still unknown and resubmission is safe")
S.msg("ph","ad","submit(batch, batchId)","sync",
      note=("contract prevents duplicates or<br>evidence shows resending is safe",X["ad"]+20,R,2))
S.msg("ad","ph","outcome","reply",note=("recorded as in steps 5 and 6",X["ad"]+20,R,1))
S.else_("else")
S.self_("ph","raise alert",w=160,
        side=("controlled handling; no automatic resend",X["att"]+30,R,1))
S.end()
S.end()
# 8 supplementary batch
S.frame("opt","Attendance applies an adjustment after handover")
S.msg("ph","att","queryAdjustments(period)","sync",step=8,activate=True)
S.msg("att","ph","applied adjustments","reply"); S.off("att")
S.self_("ph","supplementary batch",w=200,
        side=("linked to the earlier batch;<br>submitted under the same rules",X["att"]+30,R,2))
S.end()
# 9 period check
S.self_("ph","check the whole period",step=9,w=230,
        side=("compare mandatory data with CONFIRMED<br>batches; alert through HealthApi if the<br>deadline is at risk",X["att"]+30,R,3))
S.off("ph")
S.ll_end=S.y-6
open(sys.argv[1],'w').write(S.xml("Figure 4.17 Payroll handover sequence","Figure417Payroll"))
