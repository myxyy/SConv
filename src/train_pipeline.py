import torchvision
from text_loader import TextDataset
import torch
import torch.nn as nn
import pytorch_lightning as pl
import os
from pytorch_lightning.loggers import TensorBoardLogger
import hydra
from hydra.utils import instantiate
from tqdm import tqdm
from torch.distributed.pipeline.sync import Pipe

@hydra.main(version_base=None, config_path="../configs/", config_name="config")
def main(cfg):
    os.environ['MASTER_ADDR'] = 'localhost'
    os.environ['MASTER_PORT'] = '29500'
    torch.distributed.rpc.init_rpc('worker', rank=0, world_size=1)
    transforms = torchvision.transforms.Compose([])
    tokenizer = instantiate(cfg.tokenizer)
    vocab_size = tokenizer.num_tokens()
    dataset = TextDataset(cfg.train_pipeline.text, cfg.train_pipeline.length, tokenizer, transforms)
    dataloader = torch.utils.data.DataLoader(dataset, batch_size=cfg.train_pipeline.batch_size, shuffle=False, num_workers=os.cpu_count(), pin_memory=True, drop_last=True)
    ckpt_path = cfg.train_pipeline.weight
    ckpt_path = ckpt_path if os.path.isfile(ckpt_path) else None
    #trainer = pl.Trainer(devices=1, accelerator='gpu', max_epochs=cfg.train.max_epochs, log_every_n_steps=cfg.train.log_every_n_steps, logger=[TensorBoardLogger('./')])
    devices = cfg.train_pipeline.devices

    print('loading model...')

    if ckpt_path is not None:
        ckpt = torch.load(ckpt_path)
        model = instantiate(ckpt['model'])
        model = model(devices=devices, vocab_size=vocab_size)
        model.load_state_dict(ckpt['state_dict'])
        epochs = ckpt['epochs']
        steps = ckpt['steps']
        optimizer = torch.optim.AdamW(model.parameters(), lr=cfg.train_pipeline.lr)
        optimizer.load_state_dict(ckpt['optimizer'])
        del ckpt
    else:
        model = instantiate(cfg.model)
        model = model(devices=devices, vocab_size=vocab_size)
        epochs = 0
        steps = 0
        optimizer = torch.optim.AdamW(model.parameters(), lr=cfg.train_pipeline.lr)

    dtype = model.dtype

    torch.cuda.empty_cache()

    num_parameters = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"#parameter:{num_parameters}")

    model_pipe = nn.Sequential(*model.module_list())
    model_pipe = Pipe(model_pipe, chunks=cfg.train_pipeline.batch_size, checkpoint='except_last')
    model_pipe.train()

    backup_model_state_dict = {k: v.cpu() for k, v in model.state_dict().items()}
    backup_steps = steps
    backup_epochs = epochs
    backup_optimizer_state_dict = {k: v for k, v in optimizer.state_dict().items()}

    def save():
        print(f'saving... steps:{steps}/{len(dataloader)} epochs:{epochs}/{cfg.train_pipeline.max_epochs}')
        torch.save({
            'state_dict': model.state_dict(),
            'steps': steps,
            'epochs': epochs,
            'optimizer': optimizer.state_dict(),
            'model': cfg.model,
        }, cfg.train_pipeline.weight)

    def save_backup():
        print(f'saving... steps:{backup_steps}/{len(dataloader)} epochs:{backup_epochs}/{cfg.train_pipeline.max_epochs}')
        torch.save({
            'state_dict': backup_model_state_dict,
            'steps': backup_steps,
            'epochs': backup_epochs,
            'optimizer': backup_optimizer_state_dict,
            'model': cfg.model,
        }, cfg.train_pipeline.weight)

    try:
        for _ in range(cfg.train_pipeline.max_epochs - epochs):
            pbar = tqdm(dataloader, initial=steps)
            for batch in pbar:
                if steps % cfg.train_pipeline.save_every_n_steps == 0:
                    save()
                if steps % cfg.train_pipeline.backup_every_n_steps == 0:
                    #print('backup...')
                    backup_model_state_dict = {k: v.cpu() for k, v in model.state_dict().items()}
                    backup_steps = steps
                    backup_epochs = epochs
                    backup_optimizer_state_dict = {k: v for k, v in optimizer.state_dict().items()}
                model.reset_hidden()
                optimizer.zero_grad()

                text, text_next = batch
                text = text.to(devices[0])
                text_next = text_next.to(devices[-1])
                text = nn.functional.one_hot(text.long(), vocab_size).to(dtype)

                text_hat = model_pipe(text).local_value()

                loss = nn.CrossEntropyLoss()(text_hat.view(-1,vocab_size), text_next.view(-1).long())
 
                loss.backward()
                optimizer.step()
                pbar.set_postfix(loss=loss.item())
                steps += 1
            steps = 0
            epochs += 1
            save()
    except KeyboardInterrupt:
        print(f'KeyboardInterrupted')
        save_backup()


if __name__ == '__main__':
    main()