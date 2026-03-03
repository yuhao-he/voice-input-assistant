import base64
from typing import Dict, List
from PyQt6.QtCore import Qt, QBuffer, QIODevice, QPointF
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen

_frame_cache: Dict[int, List[str]] = {}

def get_conveyor_spinner_frames(height: int, num_frames: int = 31) -> List[str]:
    """
    Returns a list of HTML <img> tags containing base64-encoded PNGs
    of the Conveyor animation, scaled to the given text height.
    Caches the generated frames to ensure fast retrieval per text height.
    """
    if height in _frame_cache:
        return _frame_cache[height]

    # Double the height as per user request
    height = height * 2

    frames = []
    width = int(height * (385.0 / 200.0))
    
    target_width = 385.0
    target_height = 200.0
    scale = min(width / target_width, height / target_height) * 0.8
    center_x = width / 2.0
    center_y = height / 2.0

    pen = QPen(QColor(243, 244, 246, 200))
    pen.setWidth(45)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    
    pills = [
        (QPointF(58, 130), QPointF(118, 70)),  # Exit
        (QPointF(128, 130), QPointF(188, 70)),
        (QPointF(198, 130), QPointF(258, 70)),
        (QPointF(268, 130), QPointF(328, 70)),
        (QPointF(338, 130), QPointF(398, 70)), # Enter
    ]

    for f in range(num_frames):
        phase = f / float(num_frames)
        
        pm = QPixmap(width, height)
        pm.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        painter.translate(center_x, center_y)
        painter.scale(scale, scale)
        painter.translate(-target_width / 2.0, -target_height / 2.0)
        
        shift_x = -phase * 70.0
        
        for i, (p1, p2) in enumerate(pills):
            current_p1 = p1 + QPointF(shift_x, 0)
            current_p2 = p2 + QPointF(shift_x, 0)
            
            painter.setOpacity(1.0)
            draw_p1 = current_p1
            draw_p2 = current_p2
            
            if i == 0:
                draw_p1 = current_p1 + (current_p2 - current_p1) * phase
                if phase > 0.7:
                    painter.setOpacity(1.0 - (phase - 0.7) / 0.3)
            elif i == 4:
                draw_p2 = current_p1 + (current_p2 - current_p1) * phase
                if phase < 0.3:
                    painter.setOpacity(phase / 0.3)
            else:
                pass # standard moving pill
                
            painter.setPen(pen)
            painter.drawLine(draw_p1, draw_p2)
            
        painter.end()
        
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pm.save(buffer, "PNG")
        b64 = base64.b64encode(buffer.data()).decode()
        
        # negative vertical margins to prevent it from increasing line boundaries
        img_html = f"<img src='data:image/png;base64,{b64}' width='{width}' height='{height}' style='vertical-align: middle; margin-top: -2px;'>"
        frames.append(img_html)
        
    _frame_cache[height] = frames
    return frames
