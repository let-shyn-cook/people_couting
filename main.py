import cv2
import torch
from ultralytics import YOLO
from deep_sort_realtime.deepsort_tracker import DeepSort
import datetime
import os
from collections import defaultdict
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
import threading
import queue

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = 'hallowed-span-431104-u8-7b66ba8b7b3a.json'

credentials = Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES)

SAMPLE_SPREADSHEET_ID = '1OsL758-sVO7or_wexSTPOz9mOPzfweRTEkSQLSOdMxo'
SAMPLE_RANGE_NAME = 'Trang tính1!A:D'
STAT_SHEET_NAME = 'Trang tính2'

service = build('sheets', 'v4', credentials=credentials)

event_queue = queue.Queue()

# Hàm kiểm tra và thêm tiêu đề nếu cần thiết cho cả hai trang tính
def check_and_add_headers():
    sheet = service.spreadsheets()
    result = sheet.values().get(spreadsheetId=SAMPLE_SPREADSHEET_ID, range=SAMPLE_RANGE_NAME).execute()
    values = result.get('values', [])

    if not values or values[0] != ['Thời gian', 'Số người đi vào', 'Số người đi ra', 'Tổng số người trong phòng']:
        headers = [['Thời gian', 'Số người đi vào', 'Số người đi ra', 'Tổng số người trong phòng']]
        body = {'values': headers}
        sheet.values().append(
            spreadsheetId=SAMPLE_SPREADSHEET_ID,
            range=SAMPLE_RANGE_NAME,
            valueInputOption='RAW',
            body=body
        ).execute()

    # Kiểm tra và thêm tiêu đề cho trang tính thống kê
    stat_result = sheet.values().get(spreadsheetId=SAMPLE_SPREADSHEET_ID, range=STAT_SHEET_NAME + '!A:B').execute()
    stat_values = stat_result.get('values', [])

    if not stat_values or stat_values[0] != ['Ngày', 'Số người đến thăm']:
        stat_headers = [['Ngày', 'Số người đến thăm']]
        stat_body = {'values': stat_headers}
        sheet.values().append(
            spreadsheetId=SAMPLE_SPREADSHEET_ID,
            range=STAT_SHEET_NAME + '!A1:B1',
            valueInputOption='RAW',
            body=stat_body
        ).execute()

# Hàm ghi lại sự kiện vào Google Sheet
def log_event(in_count, out_count):
    check_and_add_headers()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    current_people = in_count - out_count
    values = [[now, in_count, out_count, current_people]]
    body = {'values': values}
    service.spreadsheets().values().append(
        spreadsheetId=SAMPLE_SPREADSHEET_ID,
        range=SAMPLE_RANGE_NAME,
        valueInputOption='RAW',
        body=body
    ).execute()
    print("Đã ghi thành công")

# Hàm cập nhật thống kê
def update_statistics(statistics):
    check_and_add_headers()
    now = datetime.datetime.now()
    day_key = now.strftime("%Y-%m-%d")

    statistics['day'][day_key] += 1

    # Cập nhật thống kê ngày
    day_values = [[day_key, statistics['day'][day_key]]]
    day_body = {'values': day_values}
    service.spreadsheets().values().append(
        spreadsheetId=SAMPLE_SPREADSHEET_ID,
        range=STAT_SHEET_NAME + '!A:B',
        valueInputOption='RAW',
        body=day_body
    ).execute()

    # Kiểm tra và cập nhật thống kê tuần
    if now.weekday() == 6:  # Nếu là Chủ nhật (ngày cuối cùng của tuần)
        week_total = sum(statistics['day'].values())
        week_values = [['Tuần này đã có', week_total]]
        week_body = {'values': week_values}
        service.spreadsheets().values().append(
            spreadsheetId=SAMPLE_SPREADSHEET_ID,
            range=STAT_SHEET_NAME + '!A:B',
            valueInputOption='RAW',
            body=week_body
        ).execute()
        statistics['day'] = defaultdict(int)  # Đặt lại thống kê ngày cho tuần mới

    # Kiểm tra và cập nhật thống kê tháng
    if now.day == now.replace(day=28).day and (now + datetime.timedelta(days=4)).month != now.month:
        month_total = sum(statistics['day'].values())
        month_values = [['Tháng này đã có', month_total]]
        month_body = {'values': month_values}
        service.spreadsheets().values().append(
            spreadsheetId=SAMPLE_SPREADSHEET_ID,
            range=STAT_SHEET_NAME + '!A:B',
            valueInputOption='RAW',
            body=month_body
        ).execute()
        statistics['day'] = defaultdict(int)  # Đặt lại thống kê ngày cho tháng mới

    # Kiểm tra và cập nhật thống kê năm
    if now.month == 12 and now.day == 31:
        year_total = sum(statistics['day'].values())
        year_values = [['Năm này đã có', year_total]]
        year_body = {'values': year_values}
        service.spreadsheets().values().append(
            spreadsheetId=SAMPLE_SPREADSHEET_ID,
            range=STAT_SHEET_NAME + '!A:B',
            valueInputOption='RAW',
            body=year_body
        ).execute()
        statistics['day'] = defaultdict(int)  # Đặt lại thống kê ngày cho năm mới

# Hàm xử lý các sự kiện trong hàng đợi
def process_event_queue():
    while True:
        event = event_queue.get()
        if event is None:
            break
        in_count, out_count, statistics = event
        log_event(in_count, out_count)
        update_statistics(statistics)
        event_queue.task_done()

# Bắt đầu luồng xử lý sự kiện
event_thread = threading.Thread(target=process_event_queue)
event_thread.start()

model = YOLO('yolov8s.onnx')
tracker = DeepSort(max_age=30)
cap = cv2.VideoCapture("TestVideo.avi")

frame_width = int(cap.get(3))
frame_height = int(cap.get(4))
line1_y = frame_height // 3
line2_y = 2 * frame_height // 3
drawing = False
line_points = []
custom_lines = False
dragging_line1 = False
dragging_line2 = False

in_count = 0
out_count = 0
previous_centers = {}
statistics = {
    'day': defaultdict(int)
}

def mouse_callback(event, x, y, flags, param):
    global drawing, line_points, dragging_line1, dragging_line2, line1_y, line2_y, custom_lines

    if custom_lines:
        if event == cv2.EVENT_LBUTTONDOWN:
            drawing = True
            line_points = [(x, y)]

        elif event == cv2.EVENT_MOUSEMOVE:
            if drawing:
                line_points.append((x, y))

        elif event == cv2.EVENT_LBUTTONUP:
            drawing = False
            line_points.append((x, y))
    else:
        if event == cv2.EVENT_LBUTTONDOWN:
            if abs(y - line1_y) < 10:
                dragging_line1 = True
            elif abs(y - line2_y) < 10:
                dragging_line2 = True

        elif event == cv2.EVENT_MOUSEMOVE:
            if dragging_line1:
                line1_y = y
            elif dragging_line2:
                line2_y = y

        elif event == cv2.EVENT_LBUTTONUP:
            dragging_line1 = False
            dragging_line2 = False

cv2.namedWindow("Realtime Detection")
cv2.setMouseCallback("Realtime Detection", mouse_callback)

def draw_button(img, text, pos):
    cv2.rectangle(img, pos, (pos[0] + 100, pos[1] + 30), (50, 50, 50), -1)
    cv2.putText(img, text, (pos[0] + 10, pos[1] + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

def check_button_click(pos, mouse_pos):
    if pos[0] <= mouse_pos[0] <= pos[0] + 100 and pos[1] <= mouse_pos[1] <= pos[1] + 30:
        return True
    return False

button1_pos = (10, frame_height - 40)
button2_pos = (120, frame_height - 40)

while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break

    results = model(frame)
    detections = results[0].boxes.data.cpu().numpy() if results and results[0].boxes is not None else []

    detect = []
    for detection in detections:
        x1, y1, x2, y2, conf, cls = detection
        if int(cls) == 0:
            detect.append([[x1, y1, x2 - x1, y2 - y1], conf, int(cls)])

    tracks = tracker.update_tracks(detect, frame=frame)

    for track in tracks:
        if track.is_confirmed():
            track_id = track.track_id
            ltrb = track.to_ltrb()
            x1, y1, x2, y2 = map(int, ltrb)
            center = (int((x1 + x2) / 2), (int((y1 + y2) / 2)))

            cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 0), 2)
            cv2.circle(frame, center, 5, (0, 255, 0), -1)
            cv2.putText(frame, f'ID: {track_id}', (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)

            if track_id in previous_centers:
                prev_center = previous_centers[track_id]
                if custom_lines and len(line_points) >= 2:
                    for i in range(len(line_points) - 1):
                        p1, p2 = line_points[i], line_points[i + 1]
                        if prev_center[1] < p1[1] <= center[1] or prev_center[1] < p2[1] <= center[1]:
                            in_count += 1
                            event_queue.put((in_count, out_count, statistics))
                        elif prev_center[1] > p1[1] >= center[1] or prev_center[1] > p2[1] >= center[1]:
                            out_count += 1
                            event_queue.put((in_count, out_count, statistics))
                else:
                    if prev_center[1] < line1_y <= center[1]:
                        in_count += 1
                        event_queue.put((in_count, out_count, statistics))
                    elif prev_center[1] > line2_y >= center[1]:
                        out_count += 1
                        event_queue.put((in_count, out_count, statistics))

            previous_centers[track_id] = center

    if custom_lines and len(line_points) >= 2:
        for i in range(len(line_points) - 1):
            cv2.line(frame, line_points[i], line_points[i + 1], (0, 255, 255), 2)
    else:
        cv2.line(frame, (0, line1_y), (frame_width, line1_y), (0, 255, 255), 2)
        cv2.line(frame, (0, line2_y), (frame_width, line2_y), (255, 255, 0), 2)

    cv2.putText(frame, f'In: {in_count}', (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
    cv2.putText(frame, f'Out: {out_count}', (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
    cv2.putText(frame, f'Current: {in_count - out_count}', (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)

    draw_button(frame, "Draw Lines", button1_pos)
    draw_button(frame, "Default Lines", button2_pos)

    cv2.imshow("Realtime Detection", frame)

    key = cv2.waitKey(1)
    if key & 0xFF == ord('q'):
        break

    mouse_pos = cv2.getWindowImageRect("Realtime Detection")
    if check_button_click(button1_pos, mouse_pos):
        custom_lines = True
        line_points = []
    elif check_button_click(button2_pos, mouse_pos):
        custom_lines = False
        line_points = []

cap.release()
cv2.destroyAllWindows()

# Dừng luồng xử lý sự kiện
event_queue.put(None)
event_thread.join()
