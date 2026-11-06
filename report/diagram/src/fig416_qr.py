"""Figure 4.16 - QR scan sequence (style v2)."""
import sys; sys.path.insert(0,'.')
from seq2 import Seq
S=Seq([("sc","client","Station Client"),("gtp","component","Gate Transaction Processor"),
       ("ad","component","Access Decision"),("ta","component","Turnstile Adapter")],
      spacing=236,head_w=218,head_h=50,fs=16,row=10,frame_hdr=27,first=28)
X=S.X; R=S.right
NR=X["gtp"]+22; R=S.right-14          # notes attached to Gate Transaction Processor start here
# 1 scan request
S.msg("sc","gtp","submitScan(scanId, QR, laneId)","sync",step=1,activate=True)
S.frame("break","scanId already stored")
S.msg("gtp","sc","stored result or state","reply",step=2,
      note=("this request ends; the first coordinator keeps the original cycle",NR,R,1))
S.end()
# 3 access checks
S.msg("gtp","ad","evaluate(QR, laneId)","sync",step=3,activate=True,
      note=("verify QR; read Eligibility Snapshot",X["ad"]+20,R,1))
S.msg("ad","gtp","decision, reason, data version","reply"); S.off("ad")
S.frame("break","access check fails or access data is unusable")
S.msg("gtp","sc","REJECTED |<br>CANNOT PROCESS","reply",lines=2,
      note=("invalid QR or no permission → REJECTED; reason and audit stored<br>unusable access data → CANNOT PROCESS; no opening command",NR,R,2))
S.end()
# 4 atomic acceptance
S.self_("gtp","attempt atomic acceptance",step=4,w=230,
        side=("reserve lane, claim tokenId and<br>store ACCEPTED together",X["ad"]+30,R,2))
S.frame("break","acceptance transaction does not commit")
S.msg("gtp","sc","LANE BUSY | REJECTED |<br>CANNOT PROCESS","reply",lines=2,
      note=("lane has an active cycle → LANE BUSY; token already claimed → REJECTED<br>write fails → CANNOT PROCESS<br>nothing partial is kept; no opening command",NR,R,3))
S.end()
# 5 opening command
S.frame("break","deadline expired before openGate")
S.msg("gtp","sc","CANNOT PROCESS","reply",
      note=("record cancellation; release lane; keep token claim; no command",NR,R,1))
S.end()
S.msg("gtp","ta","openGate(commandId, laneId, timeout_ms)","sync",step=5,activate=True)
S.msg("ta","gtp","opening outcome","reply"); S.off("ta")
S.frame("alt","OPEN_CONFIRMED")
S.self_("gtp","store confirmed opening",step=6,w=230,
        side=("store OPENED, controller evidence,<br>audit and outbox together",X["ad"]+30,R,2))
S.frame("par",None,x1=S.left+30,x2=S.right-4)
S.msg("gtp","sc","OPENED","reply",step=7,
      note=("Station Client shows the result; the 2-second target ends here",NR,R,1))
S.off("gtp")
S.else_(None)
S.msg("ta","gtp","PASSAGE","async",step=8,step_x=X["ta"]+32,activate=True,
      note=("Timely: passage + attendance +<br>audit + outbox, one transaction.<br>Late: store for review; no attendance.",X["sc"]-60,X["gtp"]-22,3),note_extra=False)
S.gap(42)
S.off("gtp")
S.else_(None)
S.msg("ta","gtp","LANE_CLEAR","async",step=9,step_x=X["ta"]+32,activate=True,
      note=("Persist CLOSE_SENT and<br>closeCommandId; send once.<br>Stored CLOSED releases the lane.",X["sc"]-60,X["gtp"]-22,3),note_extra=False)
S.msg("gtp","ta","closeGate(closeCommandId, laneId)","sync",activate=True)
S.msg("ta","gtp","CLOSED","reply"); S.off("ta")
S.off("gtp")
S.end()
S.else_("controller confirms it did not execute")
S.on("gtp")
S.msg("gtp","sc","CONTROLLER ERROR","reply",
      note=("stored; the lane is released once the controller reports it ready",NR,R,1))
S.off("gtp")
S.else_("no reliable outcome")
S.on("gtp")
S.msg("gtp","sc","UNDETERMINED","reply",
      note=("stored; keep lane and token; never resend openGate<br>query if supported; still unknown or no query → reconciliation",NR,R,2))
S.off("gtp")
S.end()
S.ll_end=S.y-6
open(sys.argv[1],'w',encoding='utf-8').write(S.xml("Figure 4.16 QR scan sequence","Figure416QR"))
