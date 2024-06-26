# -*- coding: utf-8 -*-
"""Group_Panoptic.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1OJscH01IpVRslJBr4gXtj_whJ6kaEOvG
"""

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader
from torchvision import transforms as A
from torchvision.datasets import ImageFolder
import PIL
from torchvision.utils import save_image
import numpy as np
import cv2
import matplotlib.pyplot as plt
import os
from tqdm import tqdm
from glob import glob

cv2.setNumThreads(0)
cv2.ocl.setUseOpenCL(False)

VOC_CLASSES = [
    "background",
    "aeroplane",
    "bicycle",
    "bird",
    "boat",
    "bottle",
    "bus",
    "car",
    "cat",
    "chair",
    "cow",
    "diningtable",
    "dog",
    "horse",
    "motorbike",
    "person",
    "potted plant",
    "sheep",
    "sofa",
    "train",
    "tv/monitor",
]


VOC_COLORMAP = [
    [0, 0, 0],
    [128, 0, 0],
    [0, 128, 0],
    [128, 128, 0],
    [0, 0, 128],
    [128, 0, 128],
    [0, 128, 128],
    [128, 128, 128],
    [64, 0, 0],
    [192, 0, 0],
    [64, 128, 0],
    [192, 128, 0],
    [64, 0, 128],
    [192, 0, 128],
    [64, 128, 128],
    [192, 128, 128],
    [0, 64, 0],
    [128, 64, 0],
    [0, 192, 0],
    [128, 192, 0],
    [0, 64, 128],
]

train_transform = A.Compose([
    A.Resize(width=256, height=256),
    A.ShiftScaleRotate(),
    A.HorizontalFlip(),
    A.RandomBrightnessContrast(),
    A.Blur(),
    A.Sharpen(),
    A.RGBShift(),
    A.Cutout(num_holes=5, max_h_size=25, max_w_size=25, fill_value=0),
    A.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225), max_pixel_value=255.0),

])

test_trainsform = A.Compose([
    A.Resize(width=256, height=256),
    A.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225), max_pixel_value=255.0),
    ToTensorV2(),

class My_Dataset(VOCSegmentation):
    def __init__(self, root="~/data/pascal_voc", image_set="train", download=True, transform=None):
        super().__init__(root=root, image_set=image_set, download=download, transform=transform)


    def _convert_to_segmentation_mask(mask):

        height, width = mask.shape[:2]
        segmentation_mask = np.zeros((height, width, len(VOC_COLORMAP)), dtype=np.float32)
        for label_index, label in enumerate(VOC_COLORMAP):
            segmentation_mask[:, :, label_index] = np.all(mask == label, axis=-1).astype(float)
        return segmentation_mask

    def __getitem__(self, index):
        image = cv2.imread(self.images[index])
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        mask = cv2.imread(self.masks[index])
        mask = cv2.cvtColor(mask, cv2.COLOR_BGR2RGB)
        mask = self._convert_to_segmentation_mask(mask)
        if self.transform is not None:
            transformed = self.transform(image=image, mask=mask)
            image = transformed["image"]
            mask = transformed["mask"]
        return image, mask.argmax(dim=2).squeeze()



train_dataset = My_Dataset(image_set="train", download=True)
test_dataset =  My_Dataset(image_set="val", download=True)

image, mask = train_dataset.__getitem__(10)
plt.subplot(1, 2, 1)
plt.imshow((image).permute(1, 2, 0))
plt.subplot(1, 2, 2)
plt.imshow(mask)
plt.show()
print(mask.unique())

#metrics
def intersectionAndUnionGPU(output, target, K, ignore_index=255):
    # 'K' classes, output and target sizes are N or N * L or N * H * W, each value in range 0 to K - 1.
    assert (output.dim() in [1, 2, 3])
    assert output.shape == target.shape
    output = output.view(-1)
    target = target.view(-1)
    output[target == ignore_index] = ignore_index
    intersection = output[output == target]
    area_intersection = torch.histc(intersection, bins=K, min=0, max=K-1)
    area_output = torch.histc(output, bins=K, min=0, max=K-1)
    area_target = torch.histc(target, bins=K, min=0, max=K-1)
    area_union = area_output + area_target - area_intersection
    return area_intersection, area_union, area_target

class AverageMeter(object):
    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count

#device
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

#load data
batch_size = 8
#batch_size = 4
n_workers = os.cpu_count()
print("num_workers =", n_workers)

trainloader = torch.utils.data.DataLoader(train_dataset, batch_size=batch_size,
                                          shuffle=True, num_workers=n_workers)
testloader = torch.utils.data.DataLoader(test_dataset, batch_size=batch_size,
                                          shuffle=False, num_workers=n_workers)

# Define your Panoptic Segmentation model with additional layers
class PanopticSegmentationModel(nn.Module):
    def __init__(self, num_classes):
        super(PanopticSegmentationModel, self).__init__()
        self.num_classes = num_classes

# Semantic Segmentation Branch
        self.conv1_sem = nn.Conv2d(in_channels=3, out_channels=32, kernel_size=3, padding=1)
        self.bn1_sem = nn.BatchNorm2d(32)
        self.relu1_sem = nn.ReLU()
        self.conv2_sem = nn.Conv2d(in_channels=32, out_channels=64, kernel_size=3, padding=1)
        self.bn2_sem = nn.BatchNorm2d(64)
        self.relu2_sem = nn.ReLU()
        self.conv3_sem = nn.Conv2d(in_channels=64, out_channels=128, kernel_size=3, padding=1)
        self.bn3_sem = nn.BatchNorm2d(128)
        self.relu3_sem = nn.ReLU()
        self.conv4_sem = nn.Conv2d(in_channels=128, out_channels=256, kernel_size=3, padding=1)
        self.bn4_sem = nn.BatchNorm2d(256)
        self.relu4_sem = nn.ReLU()
        self.conv5_sem = nn.Conv2d(in_channels=256, out_channels=512, kernel_size=3, padding=1)
        self.bn5_sem = nn.BatchNorm2d(512)
        self.relu5_sem = nn.ReLU()
        self.conv_out_sem = nn.Conv2d(in_channels=512, out_channels=num_classes, kernel_size=1)

# Instance Segmentation Branch
        self.conv1_ins = nn.Conv2d(in_channels=3, out_channels=32, kernel_size=3, padding=1)
        self.bn1_ins = nn.BatchNorm2d(32)
        self.relu1_ins = nn.ReLU()
        self.conv2_ins = nn.Conv2d(in_channels=32, out_channels=64, kernel_size=3, padding=1)
        self.bn2_ins = nn.BatchNorm2d(64)
        self.relu2_ins = nn.ReLU()
        self.conv3_ins = nn.Conv2d(in_channels=64, out_channels=128, kernel_size=3, padding=1)
        self.bn3_ins = nn.BatchNorm2d(128)
        self.relu3_ins = nn.ReLU()
        self.conv4_ins = nn.Conv2d(in_channels=128, out_channels=256, kernel_size=3, padding=1)
        self.bn4_ins = nn.BatchNorm2d(256)
        self.relu4_ins = nn.ReLU()
        self.conv5_ins = nn.Conv2d(in_channels=256, out_channels=512, kernel_size=3, padding=1)
        self.bn5_ins = nn.BatchNorm2d(512)
        self.relu5_ins = nn.ReLU()
        self.conv_out_ins = nn.Conv2d(in_channels=512, out_channels=1, kernel_size=1)

    def forward(self, x):
 # Semantic Segmentation Branch
        x_sem = self.conv1_sem(x)
        x_sem = self.bn1_sem(x_sem)
        x_sem = self.relu1_sem(x_sem)
        x_sem = self.conv2_sem(x_sem)
        x_sem = self.bn2_sem(x_sem)
        x_sem = self.relu2_sem(x_sem)
        x_sem = self.conv3_sem(x_sem)
        x_sem = self.bn3_sem(x_sem)
        x_sem = self.relu3_sem(x_sem)
        x_sem = self.conv4_sem(x_sem)
        x_sem = self.bn4_sem(x_sem)
        x_sem = self.relu4_sem(x_sem)
        x_sem = self.conv5_sem(x_sem)
        x_sem = self.bn5_sem(x_sem)
        x_sem = self.relu5_sem(x_sem)
        x_sem = self.conv_out_sem(x_sem)  # Output semantic mask

 # Instance Segmentation Branch
        x_ins = self.conv1_ins(x)
        x_ins = self.bn1_ins(x_ins)
        x_ins = self.relu1_ins(x_ins)
        x_ins = self.conv2_ins(x_ins)
        x_ins = self.bn2_ins(x_ins)
        x_ins = self.relu2_ins(x_ins)
        x_ins = self.conv3_ins(x_ins)
        x_ins = self.bn3_ins(x_ins)
        x_ins = self.relu3_ins(x_ins)
        x_ins = self.conv4_ins(x_ins)
        x_ins = self.bn4_ins(x_ins)
        x_ins = self.relu4_ins(x_ins)
        x_ins = self.conv5_ins(x_ins)
        x_ins = self.bn5_ins(x_ins)
        x_ins = self.relu5_ins(x_ins)
        x_ins = self.conv_out_ins(x_ins)  # Output instance mask

 # Combine both branches for panoptic mask

        panoptic_mask = torch.cat([x_sem, x_ins], dim=1)

        return panoptic_mask

num_classes = 91 # Number of semantic classes
model = PanopticSegmentationModel(num_classes)

#loss

criterion = model.losses.DiceLoss(mode="multiclass", classes=91) #diceloss = 1-dicescore

#optimizer
optimizer = torch.optim.Adam(model.parameters(), lr=1e-4,betas={0.999,0.987})
n_eps = 10000

#meter
train_loss_meter = AverageMeter()
intersection_meter = AverageMeter()
union_meter = AverageMeter()
target_meter = AverageMeter()

# Trainning LOOP


for ep in range(1, 1+n_eps):
    train_loss_meter.reset()
    intersection_meter.reset()
    union_meter.reset()
    target_meter.reset()
    model.train()

    for batch_id, (x, y) in enumerate(tqdm(trainloader), start=1):
        optimizer.zero_grad()
        n = x.shape[0]
        x = x.to(device).float()
        y = y.to(device).long()
        y_hat = model(x) #(B, C, H, W)
        loss = criterion(y_hat, y) #(B, C, H, W) >< (B, H, W)
        loss.backward()
        optimizer.step()

        #save metrics
        with torch.no_grad():
            train_loss_meter.update(loss.item())
            y_hat_mask = y_hat.argmax(dim=1).squeeze(1) # (B, C, H, W) -> (B, 1, H, W) -> (B, H, W)
            intersection, union, target = intersectionAndUnionGPU(y_hat_mask.float(), y.float(), 21)
            intersection_meter.update(intersection)
            union_meter.update(union)
            target_meter.update(target)

    #compute iou, dice
    with torch.no_grad():
        iou_class = intersection_meter.sum / (union_meter.sum + 1e-10) #vector 21D
        dice_class = (2 * intersection_meter.sum) / (intersection_meter.sum + union_meter.sum + 1e-10) #vector 21D

        mIoU = torch.mean(iou_class) #mean vector 21D
        mDice = torch.mean(dice_class) #mean vector 21D

    print("EP {}, train loss = {} IoU = {}, dice = {}".format(ep, train_loss_meter.avg, mIoU, mDice))

    if ep >= 2000:
        torch.save(model.state_dict(), "modelDeepLab_ep_{}.pth".format(ep))

# Testing LOOP

model.eval()
test_intersection_meter = AverageMeter()
test_union_meter = AverageMeter()
test_target_meter = AverageMeter()
with torch.no_grad():
    for batch_id, (x, y) in enumerate(tqdm(testloader), start=1):
        n = x.shape[0]
        x = x.to(device).float()
        y = y.to(device).long()
        y_hat = model(x)
        y_hat = y_hat.squeeze(1)
        y_hat_mask = y_hat.argmax(dim=1)

        intersection, union, target = intersectionAndUnionGPU(y_hat_mask, y, 21)
        test_intersection_meter.update(intersection)
        test_union_meter.update(union)
        test_target_meter.update(target)

    iou_class = test_intersection_meter.sum / (test_union_meter.sum + 1e-10)
    dice_class = 2*test_intersection_meter.sum / (test_intersection_meter.sum + test_union_meter.sum + 1e-10)

    mIoU = torch.mean(iou_class)
    mDice = torch.mean(dice_class)

# Save the trained model
torch.save(model.state_dict(), 'panoptic_segmentation_model.pth')

# Testing

model.eval()
test_intersection_meter = AverageMeter()
test_union_meter = AverageMeter()
test_target_meter = AverageMeter()
with torch.no_grad():
    for batch_id, (x, y) in enumerate(tqdm(testloader), start=1):
        n = x.shape[0]
        x = x.to(device).float()
        y = y.to(device).long()
        y_hat = model(x)
        y_hat = y_hat.squeeze(1)
        y_hat_mask = y_hat.argmax(dim=1)

        intersection, union, target = intersectionAndUnionGPU(y_hat_mask, y, 21)
        test_intersection_meter.update(intersection)
        test_union_meter.update(union)
        test_target_meter.update(target)

    iou_class = test_intersection_meter.sum / (test_union_meter.sum + 1e-10)
    dice_class = 2*test_intersection_meter.sum / (test_intersection_meter.sum + test_union_meter.sum + 1e-10)

    mIoU = torch.mean(iou_class)
    mDice = torch.mean(dice_class)

print("TEST: IoU = {}, dice = {}".format(mIoU, mDice))

#prediction



import random
id = random.randint(0, test_dataset.__len__())
with torch.no_grad():
    model.eval()
    x, y = test_dataset.__getitem__(id)
    y_predict = model(x.unsqueeze(0).to(device)).argmax(dim=1).squeeze()
    intersection, union, target = intersectionAndUnionGPU(y_predict.float(), y.to(device).float(), 21)
    iou_class = intersection / (union + 1e-10)
    dice_class = 2*intersection / (intersection + union + 1e-10)
    mIoU = torch.mean(iou_class)
    mDice = torch.mean(dice_class)
    print("IoU = {}\n dice = {}".format(iou_class, dice_class))
    y_predict = y_predict.cpu().numpy()
#     print(np.unique(y_predict))
    for i in np.unique(y_predict):
        print(VOC_CLASSES[i])
    color_mask_predict = np.zeros((*y_predict.shape, 3))
    for i, color in enumerate(VOC_COLORMAP):
        color_mask_predict[y_predict==i] = np.array(color)
    color_mask = np.zeros((*y_predict.shape, 3))
    for i, color in enumerate(VOC_COLORMAP):
        color_mask[y==i] = np.array(color)
    plt.subplot(1,3,1)
    plt.imshow(unorm(x).permute(1, 2, 0))
    plt.subplot(1,3,2)
    plt.imshow(color_mask)
    plt.subplot(1,3,3)
    plt.imshow(color_mask_predict)
    plt.show()