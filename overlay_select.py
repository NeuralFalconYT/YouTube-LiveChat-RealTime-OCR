import tkinter as tk
import win32api
import win32con
import ctypes

class ScreenSelector:
    def __init__(self):
        self.start_x = None
        self.start_y = None
        self.rect = None
        self.region = None

    def select_area(self):

        # Fix high DPI scaling
        ctypes.windll.user32.SetProcessDPIAware()

        # Virtual multi-monitor desktop size
        self.left   = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
        self.top    = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
        self.width  = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
        self.height = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)

        # Fullscreen overlay window
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.geometry(f"{self.width}x{self.height}+{self.left}+{self.top}")
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.25)
        self.root.configure(bg="black")

        # Canvas
        self.canvas = tk.Canvas(self.root,
                                cursor="cross",
                                bg="black",
                                highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Events
        self.canvas.bind("<ButtonPress-1>", self.on_down)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_up)

        self.root.mainloop()
        return self.region

    # -----------------------
    # CANVAS-COORD RECTANGLE
    # -----------------------

    def on_down(self, event):
        self.start_x = event.x
        self.start_y = event.y

        self.rect = self.canvas.create_rectangle(
            self.start_x, self.start_y,
            self.start_x, self.start_y,
            outline="red",
            width=3
        )

    def on_drag(self, event):
        self.canvas.coords(
            self.rect,
            self.start_x, self.start_y,
            event.x, event.y
        )

    def on_up(self, event):

        # Convert canvas coords → real screen coords
        end_x_screen = event.x_root
        end_y_screen = event.y_root

        start_x_screen = self.root.winfo_rootx() + self.start_x
        start_y_screen = self.root.winfo_rooty() + self.start_y

        self.region = (
            min(start_x_screen, end_x_screen),
            min(start_y_screen, end_y_screen),
            max(start_x_screen, end_x_screen),
            max(start_y_screen, end_y_screen)
        )

        self.root.destroy()
# selector = ScreenSelector()
# region = selector.select_area()
# print("Selected:", region)
