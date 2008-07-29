#!/usr/bin/env python
# generated by wxGlade 0.3.3 on Mon Jun 14 06:48:07 2004

import wx
import sys
sys.path.append(".")

#import viewpanel
import example

from myviewpanel import MyViewPanel

class ViewFrame(wx.Frame):
	def __init__(self, parent, title, dstack = None):
		wx.Frame.__init__(self,parent, -1, title)

		self.vp = MyViewPanel(self, dstack)
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(self.vp, 1,wx.EXPAND,0)
		self.SetAutoLayout(1)
		self.SetSizer(sizer)
		sizer.Fit(self)
		#sizer.SetSizeHints(self)

		self.Layout()        

        
    

# end of class ViewFrame


class MyApp(wx.App):
    def OnInit(self):
        #wx.InitAllImageHandlers()
        vframe = ViewFrame(None, "Hi")
        self.SetTopWindow(vframe)
        vframe.Show(1)
        return 1

# end of class MyApp

if __name__ == "__main__":
    app = MyApp(0)
    app.MainLoop()
