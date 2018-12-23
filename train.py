#!/usr/bin/env python# encoding: utf-8'''@author: wujiyang@contact: wujiyang@hust.edu.cn@file: train.py.py@time: 2018/12/21 17:37@desc: train script for deep face recognition'''import osimport torch.utils.datafrom torch import nnfrom torch.nn import DataParallelfrom datetime import datetime#from config import BATCH_SIZE,  RESUME, PRETRAIN, SAVE_DIR, MODEL_PRE, GPUfrom backbone.mobilefacenet import MobileFaceNetfrom arcface.ArcMarginProduct import ArcMarginProductfrom utils.logging import init_logfrom dataset.casia_webface import CASIAWebFacefrom dataset.lfw import LFWfrom torch.optim import lr_schedulerimport torch.optim as optimimport timefrom lfw_eval import  evaluation_10_foldimport numpy as npimport scipy.ioimport torchvision.transforms as transformsimport argparsedef train(args):    # gpu init    multi_gpus = False    if len(args.gpus.split(',')) > 1:        multi_gpus = True    os.environ['CUDA_VISIBLE_DEVICES'] = args.gpus    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')    # log init    start_epoch = 1    save_dir = os.path.join(args.save_dir, args.model_pre + 'v1_' + datetime.now().strftime('%Y%m%d_%H%M%S'))    if os.path.exists(save_dir):        raise NameError('model dir exists!')    os.makedirs(save_dir)    logging = init_log(save_dir)    _print = logging.info    # dataset loader    transform = transforms.Compose([        transforms.ToTensor(),  # range [0, 255] -> [0.0,1.0]        transforms.Normalize(mean=(0.5, 0.5, 0.5), std=(0.5, 0.5, 0.5))  # range [0.0, 1.0] -> [-1.0,1.0]    ])    # train dataset    trainset = CASIAWebFace(args.train_root, args.train_file_list, transform=transform)    trainloader = torch.utils.data.DataLoader(trainset, batch_size=args.batch_size,                                              shuffle=True, num_workers=4, drop_last=False)    testdataset = LFW(args.test_root, args.test_file_list, transform=transform)    testloader = torch.utils.data.DataLoader(testdataset, batch_size=128,                                             shuffle=False, num_workers=2, drop_last=False)    # define backbone and margin layer    if args.backbone is 'mobileface':        net = MobileFaceNet()    elif args.backbone is 'res50':        pass    elif args.backbone is 'res101':        pass    else:        print(args.backbone, 'is not available!')    if args.margin_type is 'arcface':        ArcMargin = ArcMarginProduct(128, trainset.class_nums)    elif args.margin_type is 'cosface':        pass    elif args.margin_type is 'sphereface':        pass    else:        print(args.margin_type, 'is not available!')    # resume or pretrain    if args.pretrain:        print('load pretrained model from:', args.pretrain)        # load pretrained model        net_old = MobileFaceNet()        net_old.load_state_dict(torch.load(args.pretrain)['net_state_dict'])        # filter the parameters not in new model        net_dict = net.state_dict()        net_old = {k: v for k, v in net_old.state_dict().items() if k in net_dict}        # update the new state_dict        net_dict.update(net_old)        net.load_state_dict(net_dict)    if args.resume:        print('resume the model parameters from: ', args.resume)        ckpt = torch.load(args.resume)        net.load_state_dict(ckpt['net_state_dict'])        start_epoch = ckpt['epoch'] + 1    # define optimizers for different layer    ignored_params_id = list(map(id, net.linear1.parameters()))    ignored_params_id += list(map(id, ArcMargin.weight))    prelu_params = []    for m in net.modules():        if isinstance(m, nn.PReLU):            ignored_params_id += list(map(id, m.parameters()))            prelu_params += m.parameters()    base_params = filter(lambda p: id(p) not in ignored_params_id, net.parameters())    optimizer_ft = optim.SGD([        {'params': base_params, 'weight_decay': 4e-5},        {'params': net.linear1.parameters(), 'weight_decay': 4e-4},        {'params': ArcMargin.weight, 'weight_decay': 4e-4},        {'params': prelu_params, 'weight_decay': 0.0}    ], lr=0.1, momentum=0.9, nesterov=True)    exp_lr_scheduler = lr_scheduler.MultiStepLR(optimizer_ft, milestones=[30, 50, 60], gamma=0.1)    if multi_gpus:        net = DataParallel(net).to(device)        ArcMargin = DataParallel(ArcMargin).to(device)    else:        net = net.to(device)        ArcMargin = ArcMargin.to(device)    criterion = torch.nn.CrossEntropyLoss().to(device)    best_acc = 0.0    best_epoch = 0    for epoch in range(start_epoch, args.total_epoch + 1):        exp_lr_scheduler.step()        # train model        _print('Train Epoch: {}/{} ...'.format(epoch, args.total_epoch))        net.train()        train_total_loss = 0.0        total = 0        since = time.time()        current = time.time()        iters = 0        for data in trainloader:            img, label = data[0].to(device), data[1].to(device)            batch_size = img.size(0)            optimizer_ft.zero_grad()            raw_logits = net(img)            output = ArcMargin(raw_logits, label)            total_loss = criterion(output, label)            total_loss.backward()            optimizer_ft.step()            train_total_loss += total_loss.item() * batch_size            total += batch_size            # print train information            iters = iters + 1            if iters % 100 == 0:                time_batch = (time.time() - current) / 100                current = time.time()                print("Iters: {:4d}, loss: {:.4f}, time: {:.4f} s/iter, learning rate: {}".format(iters, total_loss.item(), time_batch, exp_lr_scheduler.get_lr()[0]))        train_total_loss = train_total_loss / total        time_elapsed = time.time() - since        loss_msg = 'Total_loss: {:.4f} time: {:.0f}m {:.0f}s'.format(train_total_loss, time_elapsed // 60, time_elapsed % 60)        _print(loss_msg)        # test model on lfw        if epoch % args.test_freq == 0:            net.eval()            featureLs = None            featureRs = None            _print('Test Epoch: {} ...'.format(epoch))            for data in testloader:                for i in range(len(data)):                    data[i] = data[i].to(device)                res = [net(d).data.cpu().numpy() for d in data]                featureL = np.concatenate((res[0], res[1]), 1)                featureR = np.concatenate((res[2], res[3]), 1)                if featureLs is None:                    featureLs = featureL                else:                    featureLs = np.concatenate((featureLs, featureL), 0)                if featureRs is None:                    featureRs = featureR                else:                    featureRs = np.concatenate((featureRs, featureR), 0)            result = {'fl': featureLs, 'fr': featureRs, 'fold': testdataset.folds, 'flag': testdataset.flags}            # save tmp_result            scipy.io.savemat('./result/tmp_result.mat', result)            accs = evaluation_10_fold('./result/tmp_result.mat')            _print('Ave Accuracy: {:.4f}'.format(np.mean(accs) * 100))            if best_acc < np.mean(accs):                best_acc = np.mean(accs)                best_epoch = epoch            _print('Current Best Accuracy: {:.4f} in Epoch: {}'.format(best_acc * 100, best_epoch))        # save model        if epoch % args.save_freq == 0:            msg = 'Saving checkpoint: {}'.format(epoch)            _print(msg)            if multi_gpus:                net_state_dict = net.module.state_dict()            else:                net_state_dict = net.state_dict()            if not os.path.exists(save_dir):                os.mkdir(save_dir)            torch.save({                'epoch': epoch,                'net_state_dict': net_state_dict},                os.path.join(save_dir, '%03d.ckpt' % epoch))    _print('Best Accuracy: {:.4f} in Epoch: {}'.format(best_acc * 100, best_epoch))    print('finishing training')if __name__ == '__main__':    parser = argparse.ArgumentParser(description='PyTorch for deep face recognition')    parser.add_argument('--train_root', type=str, default='D:/data/webface_align_112', help='train image root')    parser.add_argument('--train_file_list', type=str, default='D:/data/webface_align_train_rm_200.list', help='train list')    parser.add_argument('--test_root', type=str, default='D:/data/lfw_align_112', help='test image root')    parser.add_argument('--test_file_list', type=str, default='D:/data/pairs.txt', help='test file list')    parser.add_argument('--backbone', type=str, default='mobileface', help='mobileface, res50, res101')    parser.add_argument('--margin_type', type=str, default='arcface', help='arcface, cosface, sphereface')    parser.add_argument('--batch_size', type=int, default=200, help='batch size')    parser.add_argument('--save_freq', type=int, default=1, help='save frequency')    parser.add_argument('--test_freq', type=int, default=1, help='test frequency')    parser.add_argument('--total_epoch', type=int, default=70, help='total epochs')    parser.add_argument('--resume', type=str, default='', help='resume model')    parser.add_argument('--pretrain', type=str, default='', help='pretrain model')    parser.add_argument('--save_dir', type=str, default='./model', help='model save dir')    parser.add_argument('--model_pre', type=str, default='CASIA_', help='model prefix')    parser.add_argument('--gpus', type=str, default='0', help='model prefix')    args = parser.parse_args()    train(args)