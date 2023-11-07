from spiral_conv import SpiralConv
import torchvision.transforms as transforms
import torch
import torch.nn as nn

class Lang(nn.Module):
    def __init__(self, devices, model=SpiralConv, depth=32, dropout=0.1, vocab_size=256, dim=256, dim_ff_hidden=512, dim_sc_hidden=512, enable_profiling=False, text_load_mode='cut'):
        super().__init__()
        self.text_load_mode = text_load_mode
        self.enable_profiling=enable_profiling
        self.model = model(depth, dim, dim_ff_hidden, dim_sc_hidden, dropout, devices)
        self.dim = dim
        self.vocab_size = vocab_size
        self.token_in = nn.Linear(vocab_size, dim, device=devices[0])
        self.token_out = nn.Linear(dim, vocab_size, device=devices[-1])
        self.num_parameters = sum(p.numel() for p in self.parameters() if p.requires_grad)
        self.clear_count = 0
        self.devices = devices

        self.apply(self._init_weights)
        #self.save_hyperparameters()

    def _init_weights(self, m):
        if isinstance(m, nn.Linear):
            nn.init.normal_(m.weight, std=self.num_parameters**-0.5)
            if isinstance(m, nn.Linear) and m.bias is not None:
                nn.init.normal_(m.bias, std=self.num_parameters**-0.5)
        elif isinstance(m, nn.Parameter):
            nn.init.normal_(m.weight, std=self.num_parameters**-0.5)
        elif isinstance(m, nn.LayerNorm):
            nn.init.constant_(m.bias, 0)
            nn.init.constant_(m.weight, 1.0)

    def predict_step(self, batch):
        text = batch

        text_hat = self(text)

        text_hat = nn.Softmax(dim=-1)(text_hat)

        return text_hat

    def training_step(self, batch):
        text, text_next = batch
        text = text.to(self.devices[0])
        text_next = text_next.to(self.devices[-1])

        text_hat = self(text)

        loss = nn.CrossEntropyLoss()(text_hat.view(-1,self.vocab_size), text_next.view(-1).long())

        #self.log("train_loss", loss, on_epoch=False, prog_bar=True)
        return loss

    def forward(self, x):
        x = nn.functional.one_hot(x.long(), self.vocab_size).float()
        x = self.token_in(x)
        x = self.model(x)
        x_hat = self.token_out(x)
        return x_hat

    def reset_hidden(self):
        self.model.reset_hidden()

    def randomize_init(self):
        self.model.randomize_init()

    def set_is_refresh(self, is_refresh):
        self.model.set_is_refresh(is_refresh)

    def module_list(self):
        mlist = self.model.module_list()
        mlist[0] = nn.Sequential(self.token_in, mlist[0])
        mlist[-1] = nn.Sequential(mlist[-1], self.token_out)
        return mlist