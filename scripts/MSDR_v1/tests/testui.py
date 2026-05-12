from asciimatics.screen import Screen
from asciimatics.effects import Cycle, Stars
from asciimatics.renderers import FigletText
import time
from asciimatics.scene import Scene

def demo(screen):
    # Efecto 1: Estrellas de fondo
    effects = [
        Stars(screen, screen.width),
        # Efecto 2: Texto grande "SDR" que cambia de color
        Cycle(
            screen,
            FigletText("PAGUENME BIEN INGE ", font='big'),
            int(screen.height / 2 - 4)
        )
    ]
    screen.play([Scene(effects, 500)])
Screen.wrapper(demo)