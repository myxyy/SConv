import torch
import torch.nn as nn

class FFN(nn.Module):
    def __init__(self, dim: int, dim_ff_scale: float, dropout: float):
        super().__init__()
        self.linear_1 = nn.Linear(dim, dim*dim_ff_scale, bias=True)
        self.linear_2 = nn.Linear(dim*dim_ff_scale, dim, bias=True)
        self.act = nn.SiLU()
        self.dropout = nn.Dropout(dropout)
    def forward(self, x):
        x = self.linear_1(x)
        x = self.act(x)
        x = self.linear_2(x)
        x = self.dropout(x)
        return x

class SpiralConvConvBlock(nn.Module):
    def __init__(self, dim: int):
        super().__init__()
        self.dim = dim
        self.lmlg = nn.Parameter(torch.randn(dim)) # log(-log(gamma))
        self.theta = nn.Parameter(torch.randn(dim))
        self.last_conv = None # (batch, dim)
        self.last_conv_init = nn.Parameter(torch.randn(dim)) # (dim)
        self.is_refresh = True

    # (len, batch, dim) -> (len, batch, dim)
    def forward(self, x):
        len = x.shape[0]
        batch = x.shape[1]
        if self.last_conv is None:
            self.last_conv = self.last_conv_init.expand(batch, self.dim) 
        gamma = torch.exp(-torch.exp(self.lmlg))
        c = torch.polar(gamma, self.theta)
        filter = torch.pow(c.unsqueeze(0), torch.arange(len, device=x.device).unsqueeze(1)) # (len, dim)
        filter_fft = torch.fft.fft(filter, n=len*2, dim=0) # (len*2, dim)
        x_fft = torch.fft.fft(x, n=len*2, dim=0) # (len*2, batch, dim)
        conv_filter_x = torch.fft.ifft(filter_fft.unsqueeze(1) * x_fft, dim=0).narrow(0,0,len) # (len, batch, dim)
        conv_with_past = conv_filter_x + self.last_conv.detach().unsqueeze(0)*filter.unsqueeze(1)*c.unsqueeze(0).unsqueeze(0)
        if self.is_refresh:
            self.last_conv = conv_filter_x[-1]
        
        return conv_with_past.real * x

    def clear_hidden(self):
        self.last_conv = None

    def set_is_refresh(self, is_refresh):
        self.is_refresh = is_refresh

class SpiralConvBlock(nn.Module):
    def __init__(self, dim: int, dim_ff_scale: float, dropout: float):
        super().__init__()
        self.spiral_conv = SpiralConvConvBlock(dim)
        self.ffn = FFN(dim, dim_ff_scale, dropout)
        self.layer_norm = nn.LayerNorm(dim)

    def forward(self, x):
        x_ = x
        x = self.layer_norm(x)
        x = self.spiral_conv(x)
        x = x + x_

        x_ = x
        x = self.layer_norm(x)
        x = self.ffn(x)
        x = x + x_

        return x

    def clear_hidden(self):
        self.spiral_conv.clear_hidden()

    def set_is_refresh(self, is_refresh):
        self.spiral_conv.set_is_refresh(is_refresh)

class SpiralConv(nn.Module):
    def __init__(self, depth: int, dim: int, dim_ff_scale: float, dropout: float):
        super().__init__()
        self.block_list = nn.ModuleList([SpiralConvBlock(dim, dim_ff_scale, dropout) for _ in range(depth)])

    def forward(self, x):
        for block in self.block_list:
            x = block(x)
        return x 

    def clear_hidden(self):
        for block in self.block_list:
            block.clear_hidden()

    def set_is_refresh(self, is_refresh):
        for block in self.block_list:
            block.set_is_refresh(is_refresh)
    