import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk
import torch
import torchvision
from torchvision.transforms import functional as F
import numpy as np
import cv2 as cv
import matplotlib.pyplot as plt
import math

# Load the model (Keypoint RCNN)
def get_kprcnn_model(path):
    num_keypoints = 4
    anchor_generator = torchvision.models.detection.rpn.AnchorGenerator(
        sizes=(32, 64, 128, 256, 512),
        aspect_ratios=(0.25, 0.5, 0.75, 1.0, 2.0, 3.0, 4.0),
    )
    model = torchvision.models.detection.keypointrcnn_resnet50_fpn(
        pretrained=False,
        pretrained_backbone=True,
        num_keypoints=num_keypoints,
        num_classes=2,  # Background + vertebrae class
        rpn_anchor_generator=anchor_generator,
    )
    if path:
        state_dict = torch.load(path, map_location=torch.device("cpu"))
        model.load_state_dict(state_dict)
    model.eval()  # Set model to evaluation mode
    return model

# Helper function to load and process an image
def open_image_path(path):
    img = cv.imread(path)
    img = cv.cvtColor(img, cv.COLOR_BGR2RGB)
    img_tensor = F.to_tensor(img)
    return img, img_tensor

# Filter output (convert predictions to usable format)
def filter_output(output):
    scores = output["scores"].detach().cpu().numpy()
    high_scores_idxs = np.where(scores > 0.5)[0].tolist()
    post_nms_idxs = torchvision.ops.nms(
        output["boxes"][high_scores_idxs],
        output["scores"][high_scores_idxs],
        0.3,
    ).cpu().numpy()

    np_keypoints = (
        output["keypoints"][high_scores_idxs][post_nms_idxs]
        .detach()
        .cpu()
        .numpy()
    )
    np_bboxes = (
        output["boxes"][high_scores_idxs][post_nms_idxs].detach().cpu().numpy()
    )
    np_scores = (
        output["scores"][high_scores_idxs][post_nms_idxs].detach().cpu().numpy()
    )

    sorted_scores_idxs = np.argsort(-1 * np_scores)
    np_scores = np_scores[sorted_scores_idxs]
    np_keypoints = np_keypoints[sorted_scores_idxs]
    np_bboxes = np_bboxes[sorted_scores_idxs]

    ymins = np.array([kps[0][1] for kps in np_keypoints])
    sorted_ymin_idxs = np.argsort(ymins)

    np_scores = np.array([np_scores[idx] for idx in sorted_ymin_idxs])
    np_keypoints = np.array([np_keypoints[idx] for idx in sorted_ymin_idxs])
    np_bboxes = np.array([np_bboxes[idx] for idx in sorted_ymin_idxs])

    return np_keypoints, np_bboxes, np_scores

# Cobb angle calculation functions
def _create_angles_dict(pt, mt, tl):
  """
  pt,mt,tl: tuple(2) that contains: (angle, [idxTop, idxBottom])
  """
  return {
    "pt": {
        "angle": pt[0],
        "idxs": [pt[1][0], pt[1][1]],
    },
    "mt": {
        "angle": mt[0],
        "idxs": [mt[1][0], mt[1][1]],
    },
    "tl": {
        "angle": tl[0],
        "idxs": [tl[1][0], tl[1][1]],
    }
  }

def _isS(p):
    num = len(p)
    ll = np.zeros([num-2, 1])
    for i in range(num-2):
        ll[i] = (p[i][1]-p[num-1][1])/(p[0][1]-p[num-1][1]) - (p[i][0]-p[num-1][0])/(p[0][0]-p[num-1][0])

    flag = np.sum(np.sum(np.dot(ll, ll.T))) != np.sum(np.sum(abs(np.dot(ll, ll.T))))
    return flag

def cobb_angle_cal(landmark_xy, image_shape):
    landmark_xy = list(landmark_xy)  # input is list
    ap_num = int(len(landmark_xy) / 2)  # number of points
    vnum = int(ap_num / 4)  # number of vertebrae

    first_half = landmark_xy[:ap_num]
    second_half = landmark_xy[ap_num:]

    # Values this function returns
    cob_angles = np.zeros(3)
    angles_with_pos = {}
    curve_type = None

    # Midpoints (2 points per vertebra)
    mid_p_v = []
    for i in range(int(len(landmark_xy) / 4)):
        x = first_half[2 * i: 2 * i + 2]
        y = second_half[2 * i: 2 * i + 2]
        row = [(x[0] + x[1]) / 2, (y[0] + y[1]) / 2]
        mid_p_v.append(row)

    mid_p = []
    for i in range(int(vnum)):
        x = first_half[4 * i: 4 * i + 4]
        y = second_half[4 * i: 4 * i + 4]
        point1 = [(x[0] + x[2]) / 2, (y[0] + y[2]) / 2]
        point2 = [(x[3] + x[1]) / 2, (y[3] + y[1]) / 2]
        mid_p.append(point1)
        mid_p.append(point2)

    # Line and Slope
    vec_m = []
    for i in range(int(len(mid_p) / 2)):
        points = mid_p[2 * i: 2 * i + 2]
        row = [points[1][0] - points[0][0], points[1][1] - points[0][1]]
        vec_m.append(row)

    mod_v = []
    for i in vec_m:
        row = [i[0] * i[0], i[1] * i[1]]
        mod_v.append(row)
    dot_v = np.dot(np.matrix(vec_m), np.matrix(vec_m).T)
    mod_v = np.sqrt(np.sum(np.matrix(mod_v), axis=1))

    slopes = []
    for i in vec_m:
        slope = i[1] / i[0]
        slopes.append(slope)

    angles = np.clip(dot_v / np.dot(mod_v, mod_v.T), -1, 1)
    angles = np.arccos(angles)

    maxt = np.amax(angles, axis=0)
    pos1 = np.argmax(angles, axis=0)

    pt, pos2 = np.amax(maxt), np.argmax(maxt)

    pt = pt * 180 / math.pi
    cob_angles[0] = pt

    if not _isS(mid_p_v):
        # Case: Spine Type C
        mod_v1 = np.sqrt(np.sum(np.multiply(np.matrix(vec_m[0]), np.matrix(vec_m[0]))))
        mod_vs1 = np.sqrt(np.sum(np.multiply(np.matrix(vec_m[pos2]), np.matrix(vec_m[pos2])), axis=1))
        mod_v2 = np.sqrt(np.sum(np.multiply(np.matrix(vec_m[int(vnum - 1)]), np.matrix(vec_m[int(vnum - 1)])), axis=1))
        mod_vs2 = np.sqrt(np.sum(np.multiply(vec_m[pos1.item((0, pos2))], vec_m[pos1.item((0, pos2))])))

        dot_v1 = np.dot(np.array(vec_m[0]), np.array(vec_m[pos2]).T)
        dot_v2 = np.dot(np.array(vec_m[int(vnum - 1)]), np.array(vec_m[pos1.item((0, pos2))]).T)

        mt = np.arccos(np.clip(dot_v1 / np.dot(mod_v1, mod_vs1.T), -1, 1))
        tl = np.arccos(np.clip(dot_v2 / np.dot(mod_v2, mod_vs2.T), -1, 1))

        mt = mt * 180 / math.pi
        tl = tl * 180 / math.pi
        cob_angles[1] = mt
        cob_angles[2] = tl

        angles_with_pos = _create_angles_dict(
            mt=(float(mt), [pos2, pos1.A1.tolist()[pos2]]),
            pt=(float(pt), [0, int(pos2)]),
            tl=(float(tl), [pos1.A1.tolist()[pos2], vnum - 1])
        )
        curve_type = "C"

    else:
        # Case: Spine Type S
        if ((mid_p_v[pos2 * 2][1]) + mid_p_v[pos1.item((0, pos2)) * 2][1]) < image_shape[0]:
            # Calculate Upside Cobb Angle
            mod_v_p = np.sqrt(np.sum(np.multiply(vec_m[pos2], vec_m[pos2])))
            mod_v1 = np.sqrt(np.sum(np.multiply(vec_m[0:pos2], vec_m[0:pos2]), axis=1))
            dot_v1 = np.dot(np.array(vec_m[pos2]), np.array(vec_m[0:pos2]).T)

            angles1 = np.arccos(np.clip(dot_v1 / np.dot(mod_v_p, mod_v1.T), -1, 1))
            CobbAn1, pos1_1 = np.amax(angles1, axis=0), np.argmax(angles1, axis=0)
            mt = CobbAn1 * 180 / math.pi
            cob_angles[1] = mt

            # Calculate Downside Cobb Angle
            mod_v_p2 = np.sqrt(np.sum(np.multiply(vec_m[pos1.item((0, pos2))], vec_m[pos1.item((0, pos2))])))
            mod_v2 = np.sqrt(np.sum(np.multiply(vec_m[pos1.item((0, pos2)):int(vnum)], vec_m[pos1.item((0, pos2)):int(vnum)]), axis=1))
            dot_v2 = np.dot(np.array(vec_m[pos1.item((0, pos2))]), np.array(vec_m[pos1.item((0, pos2)):int(vnum)]).T)

            angles2 = np.arccos(np.clip(dot_v2 / np.dot(mod_v_p2, mod_v2.T), -1, 1))
            CobbAn2, pos1_2 = np.amax(angles2, axis=0), np.argmax(angles2, axis=0)
            tl = CobbAn2 * 180 / math.pi
            cob_angles[2] = tl

            pos1_2 = pos1_2 + pos1.item((0, pos2)) - 1

            angles_with_pos = _create_angles_dict(
                mt=(float(pt), [pos2, pos1.A1.tolist()[pos2]]),
                pt=(float(mt), [int(pos1_1), int(pos2)]),
                tl=(float(tl), [pos1.A1.tolist()[pos2], int(pos1_2)])
            )
            curve_type = "S"

        else:
            # Calculate Upper Upside Cobb Angle
            mod_v_p = np.sqrt(np.sum(np.multiply(vec_m[pos2], vec_m[pos2])))
            mod_v1 = np.sqrt(np.sum(np.multiply(vec_m[0:pos2], vec_m[0:pos2]), axis=1))
            dot_v1 = np.dot(np.array(vec_m[pos2]), np.array(vec_m[0:pos2]).T)

            angles1 = np.arccos(np.clip(dot_v1 / np.dot(mod_v_p, mod_v1.T), -1, 1))
            CobbAn1 = np.amax(angles1, axis=0)
            pos1_1 = np.argmax(angles1, axis=0)
            mt = CobbAn1 * 180 / math.pi
            cob_angles[1] = mt

            # Calculate Upper Cobb Angle
            mod_v_p2 = np.sqrt(np.sum(np.multiply(vec_m[pos1_1], vec_m[pos1_1])))
            mod_v2 = np.sqrt(np.sum(np.multiply(vec_m[0:pos1_1 + 1], vec_m[0:pos1_1 + 1]), axis=1))
            dot_v2 = np.dot(np.array(vec_m[pos1_1]), np.array(vec_m[0:pos1_1 + 1]).T)

            angles2 = np.arccos(np.clip(dot_v2 / np.dot(mod_v_p2, mod_v2.T), -1, 1))
            CobbAn2, pos1_2 = np.amax(angles2, axis=0), np.argmax(angles2, axis=0)
            tl = CobbAn2 * 180 / math.pi
            cob_angles[2] = tl

            angles_with_pos = _create_angles_dict(
                tl=(float(pt), [pos2, pos1.A1.tolist()[pos2]]),
                mt=(float(mt), [pos1_1, pos2]),
                pt=(float(tl), [int(pos1_2), int(pos1_1)])
            )
            curve_type = "S"

    midpoint_lines = []
    for i in range(0, int(len(mid_p) / 2)):
        midpoint_lines.append([list(map(int, mid_p[i * 2])), list(map(int, mid_p[i * 2 + 1]))])

    # Remove Numpy Values
    cobb_angles_list = [float(c) for c in cob_angles]
    for key in angles_with_pos.keys():
        angles_with_pos[key]['angle'] = float(angles_with_pos[key]['angle'])
        for i in range(len(angles_with_pos[key]['idxs'])):
            angles_with_pos[key]['idxs'][i] = int(angles_with_pos[key]['idxs'][i])

    return cobb_angles_list, angles_with_pos, curve_type, midpoint_lines
def keypoints_to_landmark_xy(keypoints):
    """
    Converts keypoints (from model)
    [
        [
            [x,y],[x,y],[x,y],[x,y]
        ]
    ]
    to
    [x1,x2,x3,...,xn,y1,y2,y3,...,yn]
    """
    x_points = []
    for kps in keypoints:
        for kp in kps:
            x_points.append(kp[0])

    y_points = []
    for kps in keypoints:
        for kp in kps:
            y_points.append(kp[1])

    landmark_xy = x_points + y_points
    print("Landmark XY:", landmark_xy)  # Debugging: Print landmark_xy
    return landmark_xy

# GUI Application
def open_file():
    file_path = filedialog.askopenfilename(
        filetypes=[("Image files", "*.jpg;*.jpeg;*.png")]
    )
    if not file_path:
        return

    try:
        img, img_tensor = open_image_path(file_path)
        with torch.no_grad():
            outputs = model([img_tensor])[0]

        keypoints, _, _ = filter_output(outputs)
        if len(keypoints) == 0:
            messagebox.showerror("Error", "No keypoints detected in the image.")
            return

        landmark_xy = keypoints_to_landmark_xy(keypoints)
        cobb_results = cobb_angle_cal(landmark_xy, img.shape)
        cobb_angles, angles_with_pos, curve_type, mid_points = cobb_results

        for mp_line in mid_points:
            img = cv.line(img, tuple(mp_line[0]), tuple(mp_line[1]), (0, 255, 0), 2)

        top, bot = angles_with_pos['pt']['idxs']
        img = cv.line(img, tuple(mid_points[top][0]), tuple(mid_points[top][1]), (0, 255, 255), 5)
        img = cv.line(img, tuple(mid_points[bot][0]), tuple(mid_points[bot][1]), (0, 255, 255), 5)

        # 4. Call the Cobb Angle Calculation and Visualize
        top, bot = angles_with_pos['tl']['idxs']
        img = cv.line(img, tuple(mid_points[top][0]), tuple(mid_points[top][1]), (255, 0, 255), 5)
        img = cv.line(img, tuple(mid_points[bot][0]), tuple(mid_points[bot][1]), (255, 0, 255), 5)

        top, bot = angles_with_pos['mt']['idxs']
        img = cv.line(img, tuple(mid_points[top][0]), tuple(mid_points[top][1]), (0, 0, 255), 5)
        img = cv.line(img, tuple(mid_points[bot][0]), tuple(mid_points[bot][1]), (0, 0, 255), 5)

        print("Curve Type:", curve_type)
        print("Angles:", cobb_angles)
        
        plt.figure(figsize=(12, 12))
        plt.axis("off")
        plt.imshow(img)
        plt.show()

    except Exception as e:
        messagebox.showerror("Error", str(e))

# Initialize Tkinter App
model_path = "C:\\Users\\jarau\\OneDrive\\Desktop\\yolov5\\keypointsrcnn_weights.pt"
model = get_kprcnn_model(model_path)

root = tk.Tk()
root.title("Cobb Angle Calculation")
root.geometry("400x200")

open_button = tk.Button(root, text="Open Image", command=open_file)
open_button.pack(pady=20)

root.mainloop()