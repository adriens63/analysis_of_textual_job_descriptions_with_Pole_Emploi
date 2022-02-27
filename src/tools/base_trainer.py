import torch
from torchsummary import summary
from torch.utils.tensorboard import SummaryWriter
from sklearn.metrics import classification_report
import pandas as pd
import numpy as np
from tqdm import tqdm
import os
import os.path as osp
from datasets import load_metric
import json
from abc import ABC, abstractmethod

from src.tools.model_summary import summary_parameters
from src.tools.timer import timeit






# ********************* trainer *********************


class BaseTrainer(ABC):

    def __init__(
            self,
            device,
            model,
            epochs,
            batch_size,
            loss_fn,
            optimizer,
            lr_scheduler,
            patience,
            train_data_loader,
            train_steps,
            val_data_loader,
            val_steps,
            checkpoint_frequency,
            model_name,
            weights_path,
            ) -> None:
        
        self.device = device
        self.model = model.to(device)
        self.epochs = epochs
        self.batch_size = batch_size
        self.loss_fn = loss_fn
        self.optimizer = optimizer
        self.lr_scheduler = lr_scheduler
        self.patience = patience
        self.train_data_loader = train_data_loader
        self.train_steps = train_steps
        self.val_data_loader = val_data_loader
        self.val_steps = val_steps
        self.checkpoint_frequency = checkpoint_frequency
        self.model_name = model_name
        self.weights_path = weights_path
        self.mod_dir = weights_path + model_name + '/'
        self.log_dir = weights_path + model_name + '/log_dir/'
        self.ckp_dir = weights_path + model_name + '/ckp_dir/'

        self.metric = load_metric('accuracy')
        self.tmp_metric = load_metric('accuracy')
        self.loss = {"train": [], "val": []}
        self.acc = {"train": [], "val": []}
        self.w = SummaryWriter(log_dir = self.log_dir)
        self.last_loss = np.inf
        self.trigger_times = 0



    def train(self) -> None:

        self._summary()
        self._write_graph()


        print('.... Start training')

        for e in range(self.epochs):

            self._train_step()
            self._val_step()
            self._epoch_summary(e)
            self._write_metrics(e)
            
            if self._early_stopping():
                break


            if self.lr_scheduler is not None:

                self.lr_scheduler.step()

            if self.checkpoint_frequency:

                if not osp.exists(self.ckp_dir):
                    os.makedirs(self.ckp_dir)

                self._save_checkpoint(e)


        self.w.flush()
        self.w.close()

        print('done;')
        print()


    @abstractmethod
    def _train_step(self) -> None:
        
        pass


    @abstractmethod
    def _val_step(self) -> None:

        pass



    @timeit
    def _write_metrics(self, epoch: int) -> None:

        print('.... Saving metrics to tensorboard')
        self.w.add_scalar('loss/train', self.loss['train'][-1], epoch)
        self.w.add_scalar('loss/val', self.loss['val'][-1], epoch)

        self.w.add_scalar('acc/train', self.acc['train'][-1], epoch)
        self.w.add_scalar('acc/val', self.acc['val'][-1], epoch)

        self.w.add_scalars('losses', {'train_loss': self.loss['train'][-1],
                                        'val_loss': self.loss['val'][-1]}, epoch)
        
        self.w.add_scalars('accs', {'train_acc': self.acc['train'][-1],
                                        'val_acc': self.acc['val'][-1]}, epoch)

        for name, param in self.model.named_parameters():

            self.w.add_histogram(name, param, epoch)
        print('done;')
        print()



    @timeit
    def _write_graph(self) -> None:

        print('.... Start writing graph')
        self.model.eval()

        dummy_input = torch.zeros(size = [2, 1], dtype = torch.long).to(self.device)
        list_inp = [dummy_input, dummy_input, dummy_input]

        self.w.add_graph(self.model, input_to_model = list_inp, verbose = False)
        print('done;')
        print()



    @timeit
    def _summary(self) -> None:

        print('Summary: ')
        summary_parameters(self.model) # or summary
        print('done;')
        print()



    def _epoch_summary(self, epoch: int) -> None:

        print(
            "Epoch: {}/{}, Train Loss={:.5f}, Val Loss={:.5f}".format(
                epoch + 1,
                self.epochs,
                self.loss["train"][-1],
                self.loss["val"][-1]))



    @timeit
    def _save_checkpoint(self, epoch: int) -> None:
        """Save model checkpoint to `self.model_dir` directory"""

        epoch_num = epoch + 1
        if epoch_num % self.checkpoint_frequency == 0:
            
            print('.... Saving ckp')
            model_path = "checkpoint_{}.pt".format(str(epoch_num).zfill(3))
            model_path = osp.join(self.ckp_dir, model_path)
            torch.save({
                        'model_state_dict': self.model.state_dict(),
                        'optimizer_state_dict': self.optimizer.state_dict(),
                        'epoch': epoch
                    }, model_path)
            print('done;')
            print()



    @timeit
    def _early_stopping(self):

        self.current_loss = self.loss["val"][-1]
        print(f'Current loss: {self.current_loss}')

        if self.current_loss > self.last_loss:
            self.trigger_times += 1
            print(f'Trigger times: {self.trigger_times}')

            if self.trigger_times >= self.patience:
                print('Early stopping\nStart to test process.')
                
                return True

        else:
            self.trigger_times = 0

        self.last_loss = self.current_loss

        return False



    @timeit
    def classification_report(self) -> None:

        self.model.eval()

        batch = next(iter(self.val_data_loader))

        ids = batch['input_ids'].to(self.device)
        msk = batch['attention_mask'].to(self.device)
        lbl = batch['labels'].to(self.device)

        with torch.no_grad():

            _, out = self.model(ids, attention_mask = msk, labels = lbl)

        out = torch.argmax(out, axis = -1)

        flatten_lbl = lbl.view(-1) 
        flatten_pred = out.view(-1) 

        print(flatten_pred.shape)
        print(flatten_lbl.shape)

        print(classification_report(flatten_lbl.cpu(), flatten_pred.cpu()))
        print(pd.crosstab(flatten_lbl.cpu(), flatten_pred.cpu()))



    @timeit
    def save_model(self) -> None:

        print('.... Saving model')
        model_path = osp.join(self.weights_path, self.model_name)
        
        if not osp.exists(model_path):
            
            os.makedirs(model_path)

        torch.save(self.model, model_path + '/' + self.model_name + '.pt')

        print('done;')
        print()


    
    @timeit
    def save_loss(self) -> None:

        """Save train/val loss as json file to `self.model_dir` directory"""
        loss_path = osp.join(self.mod_dir, "loss.json")
        with open(loss_path, "w") as fp:
            
            json.dump(self.loss, fp)
            json.dump(self.acc, fp)
