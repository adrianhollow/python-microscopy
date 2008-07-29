class FakePiezo:
    def __init__(self, maxtravel = 100.00):
        self.max_travel = maxtravel
        self.curpos = maxtravel/2.0
        
    def MoveTo(self, iChannel, fPos, bTimeOut=True):
        if (fPos >= 0):
            if (fPos <= self.max_travel):
                self.curpos=fPos
            else:
                self.curpos=self.max_travel
        else:
            self.curpos=0

    def GetPos(self, iChannel=1):
        return self.curpos

    def GetControlReady(self):
        return True

    def GetChannelObject(self):
        return 1

    def GetChannelPhase(self):
        return 1

    def GetMin(self,iChan=1):
        return 0

    def GetMax(self, iChan=1):
        return self.max_travel
