#!/usr/bin/env python
# coding: utf-8

# # Machine translation
# 
# The goal is to build a machine translation model.
# By comparing the performance of three different architectures:
# * A vanilla RNN
# * A GRU-RNN
# * A transformer
# 
# The source sentences are in english and the target language is french.
# 
# Do not forget to **select the runtime type as GPU!**
# 
# **Sources**
# 
# * Dataset: [Tab-delimited Bilingual Sentence Pairs](http://www.manythings.org/anki/)
# 
# <!---
# M. Cettolo, C. Girardi, and M. Federico. 2012. WIT3: Web Inventory of Transcribed and Translated Talks. In Proc. of EAMT, pp. 261-268, Trento, Italy. pdf, bib. [paper](https://aclanthology.org/2012.eamt-1.60.pdf). [website](https://wit3.fbk.eu/2016-01).
# -->
# 
# * The code is inspired by this [pytorch tutorial](https://pytorch.org/tutorials/beginner/torchtext_translation_tutorial.html).
# 
# *This notebook is quite big, use the table of contents to easily navigate through it.*

# # Imports and data initializations
# 
# We first download and parse the dataset. From the parsed sentences
# we can build the vocabularies and the torch datasets.
# The end goal of this section is to have an iterator
# that can yield the pairs of translated datasets, and
# where each sentences is made of a sequence of tokens.

# ## Imports

# In[ ]:


get_ipython().system('python3 -m spacy download en > /dev/null')
get_ipython().system('python3 -m spacy download fr > /dev/null')
get_ipython().system('pip install torchinfo > /dev/null')
get_ipython().system('pip install einops > /dev/null')
get_ipython().system('pip install wandb > /dev/null')


from itertools import takewhile
from collections import Counter, defaultdict

import numpy as np
from sklearn.model_selection import train_test_split
import pandas as pd

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data.dataset import Dataset
from torch.utils.data import DataLoader
from torch.nn.utils.rnn import pad_sequence

import torchtext
from torchtext.data.utils import get_tokenizer
from torchtext.vocab import build_vocab_from_iterator, Vocab
from torchtext.datasets import IWSLT2016

import einops
import wandb
from torchinfo import summary

DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')


# The tokenizers are objects that are able to divide a python string into a list of tokens (words, punctuations, special tokens...) as a list of strings.
# 
# The special tokens are used for a particular reasons:
# * *\<unk\>*: Replace an unknown word in the vocabulary by this default token
# * *\<pad\>*: Virtual token used to as padding token so a batch of sentences can have a unique length
# * *\<bos\>*: Token indicating the beggining of a sentence in the target sequence
# * *\<eos\>*: Token indicating the end of a sentence in the target sequence

# In[ ]:


# Original dataset, but there's a bug on Colab with it
# train, valid, _ = IWSLT2016(language_pair=('fr', 'en'))
# train, valid = list(train), list(valid)

# Another dataset, but it is too huge
# !wget https://www.statmt.org/wmt14/training-monolingual-europarl-v7/europarl-v7.en.gz
# !wget https://www.statmt.org/wmt14/training-monolingual-europarl-v7/europarl-v7.fr.gz
# !gunzip europarl-v7.en.gz
# !gunzip europarl-v7.fr.gz

# with open('europarl-v7.en', 'r') as my_file:
#     english = my_file.readlines()

# with open('europarl-v7.fr', 'r') as my_file:
#     french = my_file.readlines()

# dataset = [
#     (en, fr)
#     for en, fr in zip(english, french)
# ]
# print(f'\n{len(dataset):,} sentences.')

# dataset, _ = train_test_split(dataset, test_size=0.8, random_state=0)  # Remove 80% of the dataset (it would be huge otherwise)
# train, valid = train_test_split(dataset, test_size=0.2, random_state=0)  # Split between train and validation dataset

# Our current dataset
get_ipython().system('wget http://www.manythings.org/anki/fra-eng.zip')
get_ipython().system('unzip fra-eng.zip')


df = pd.read_csv('fra.txt', sep='\t', names=['english', 'french', 'attribution'])
train = [
    (en, fr) for en, fr in zip(df['english'], df['french'])
]
train, valid = train_test_split(train, test_size=0.1, random_state=0)
print(len(train))

en_tokenizer, fr_tokenizer = get_tokenizer('spacy', language='en'), get_tokenizer('spacy', language='fr')

SPECIALS = ['<unk>', '<pad>', '<bos>', '<eos>']


# ## Datasets
# 
# Functions and classes to build the vocabularies and the torch datasets.
# The vocabulary is an object able to transform a string token into the id (an int) of that token in the vocabulary.

# In[ ]:


class TranslationDataset(Dataset):
    def __init__(
            self,
            dataset: list,
            en_vocab: Vocab,
            fr_vocab: Vocab,
            en_tokenizer,
            fr_tokenizer,
        ):
        super().__init__()

        self.dataset = dataset
        self.en_vocab = en_vocab
        self.fr_vocab = fr_vocab
        self.en_tokenizer = en_tokenizer
        self.fr_tokenizer = fr_tokenizer

    def __len__(self):
        """Return the number of examples in the dataset.
        """
        return len(self.dataset)

    def __getitem__(self, index: int) -> tuple:
        """Return a sample.

        Args
        ----
            index: Index of the sample.

        Output
        ------
            en_tokens: English tokens of the sample, as a LongTensor.
            fr_tokens: French tokens of the sample, as a LongTensor.
        """
        # Get the strings
        en_sentence, fr_sentence = self.dataset[index]

        # To list of words
        # We also add the beggining-of-sentence and end-of-sentence tokens
        en_tokens = ['<bos>'] + self.en_tokenizer(en_sentence) + ['<eos>']
        fr_tokens = ['<bos>'] + self.fr_tokenizer(fr_sentence) + ['<eos>']

        # To list of tokens
        en_tokens = self.en_vocab(en_tokens)  # list[int]
        fr_tokens = self.fr_vocab(fr_tokens)

        return torch.LongTensor(en_tokens), torch.LongTensor(fr_tokens)


def yield_tokens(dataset, tokenizer, lang):
    """Tokenize the whole dataset and yield the tokens.
    """
    assert lang in ('en', 'fr')
    sentence_idx = 0 if lang == 'en' else 1

    for sentences in dataset:
        sentence = sentences[sentence_idx]
        tokens = tokenizer(sentence)
        yield tokens


def build_vocab(dataset: list, en_tokenizer, fr_tokenizer, min_freq: int):
    """Return two vocabularies, one for each language.
    """
    en_vocab = build_vocab_from_iterator(
        yield_tokens(dataset, en_tokenizer, 'en'),
        min_freq=min_freq,
        specials=SPECIALS,
    )
    en_vocab.set_default_index(en_vocab['<unk>'])  # Default token for unknown words

    fr_vocab = build_vocab_from_iterator(
        yield_tokens(dataset, fr_tokenizer, 'fr'),
        min_freq=min_freq,
        specials=SPECIALS,
    )
    fr_vocab.set_default_index(fr_vocab['<unk>'])

    return en_vocab, fr_vocab


def preprocess(
        dataset: list,
        en_tokenizer,
        fr_tokenizer,
        max_words: int,
    ) -> list:
    """Preprocess the dataset.
    Remove samples where at least one of the sentences are too long.
    Those samples takes too much memory.
    Also remove the pending '\n' at the end of sentences.
    """
    filtered = []

    for en_s, fr_s in dataset:
        if len(en_tokenizer(en_s)) >= max_words or len(fr_tokenizer(fr_s)) >= max_words:
            continue

        en_s = en_s.replace('\n', '')
        fr_s = fr_s.replace('\n', '')

        filtered.append((en_s, fr_s))

    return filtered


def build_datasets(
        max_sequence_length: int,
        min_token_freq: int,
        en_tokenizer,
        fr_tokenizer,
        train: list,
        val: list,
    ) -> tuple:
    """Build the training, validation and testing datasets.
    It takes care of the vocabulary creation.

    Args
    ----
        - max_sequence_length: Maximum number of tokens in each sequences.
            Having big sequences increases dramatically the VRAM taken during training.
        - min_token_freq: Minimum number of occurences each token must have
            to be saved in the vocabulary. Reducing this number increases
            the vocabularies's size.
        - en_tokenizer: Tokenizer for the english sentences.
        - fr_tokenizer: Tokenizer for the french sentences.
        - train and val: List containing the pairs (english, french) sentences.


    Output
    ------
        - (train_dataset, val_dataset): Tuple of the two TranslationDataset objects.
    """
    datasets = [
        preprocess(samples, en_tokenizer, fr_tokenizer, max_sequence_length)
        for samples in [train, val]
    ]

    en_vocab, fr_vocab = build_vocab(datasets[0], en_tokenizer, fr_tokenizer, min_token_freq)

    datasets = [
        TranslationDataset(samples, en_vocab, fr_vocab, en_tokenizer, fr_tokenizer)
        for samples in datasets
    ]

    return datasets


# In[ ]:


def generate_batch(data_batch: list, src_pad_idx: int, tgt_pad_idx: int) -> tuple:
    """Add padding to the given batch so that all
    the samples are of the same size.

    Args
    ----
        data_batch: List of samples.
            Each sample is a tuple of LongTensors of varying size.
        src_pad_idx: Source padding index value.
        tgt_pad_idx: Target padding index value.

    Output
    ------
        en_batch: Batch of tokens for the padded english sentences.
            Shape of [batch_size, max_en_len].
        fr_batch: Batch of tokens for the padded french sentences.
            Shape of [batch_size, max_fr_len].
    """
    en_batch, fr_batch = [], []
    for en_tokens, fr_tokens in data_batch:
        en_batch.append(en_tokens)
        fr_batch.append(fr_tokens)

    en_batch = pad_sequence(en_batch, padding_value=src_pad_idx, batch_first=True)
    fr_batch = pad_sequence(fr_batch, padding_value=tgt_pad_idx, batch_first=True)
    return en_batch, fr_batch


# # Models architecture
# This is where you have to code the architectures.
# 
# In a machine translation task, the model takes as input the whole
# source sentence along with the current known tokens of the target,
# and predict the next token in the target sequence.
# This means that the target tokens are predicted in an autoregressive
# manner, starting from the first token (right after the *\<bos\>* token) and producing tokens one by one until the last *\<eos\>* token.
# 
# Formally, we define $s = [s_1, ..., s_{N_s}]$ as the source sequence made of $N_s$ tokens.
# We also define $t^i = [t_1, ..., t_i]$ as the target sequence at the beginning of the step $i$.
# 
# The output of the model parameterized by $\theta$ is:
# 
# $$
# T_{i+1} = p(t_{i+1} | s, t^i ; \theta )
# $$
# 
# Where $T_{i+1}$ is the distribution of the next token $t_{i+1}$.
# 
# The loss is simply a *cross entropy loss* over the whole steps, where each class is a token of the vocabulary.
# 
# ![RNN schema for machinea translation](https://www.simplilearn.com/ice9/free_resources_article_thumb/machine-translation-model-with-encoder-decoder-rnn.jpg)
# 
# Note that in this image the english sentence is provided in reverse.
# 
# ---
# 
# In pytorch, there is no dinstinction between an intermediate layer or a whole model having multiple layers in itself.
# Every layers or models inherit from the `torch.nn.Module`.
# This module needs to define the `__init__` method where you instanciate the layers,
# and the `forward` method where you decide how the inputs and the layers of the module interact between them.
# Thanks to the autograd computations of pytorch, you do not have
# to implement any backward method!
# 
# A really important advice is to **always look at
# the shape of your input and your output.**
# From that, you can often guess how the layers should interact
# with the inputs to produce the right output.
# You can also easily detect if there's something wrong going on.
# 
# You are more than advised to use the `einops` library and the `torch.einsum` function. This will require less operations than 'classical' code, but note that it's a bit trickier to use.
# This is a way of describing tensors manipulation with strings, bypassing the multiple tensor methods executed in the background.
# You can find a nice presentation of `einops` [here](https://einops.rocks/1-einops-basics/).
# A paper has just been released about einops [here](https://paperswithcode.com/paper/einops-clear-and-reliable-tensor).
# 
# **A great tutorial on pytorch can be found [here](https://stanford.edu/class/cs224n/materials/CS224N_PyTorch_Tutorial.html).**
# Spending 3 hours on this tutorial is *no* waste of time.

# ## RNN models

# ### RNN
# Here you have to implement a recurrent neural network. You will need to create a single RNN Layer, and a module allowing to stack these layers. Look up the pytorch documentation to figure out this module's operations and what is communicated from one layer to another.
# 
# The `RNNCell` layer produce one hidden state vector for each sentence in the batch
# (useful for the output of the encoder), and also produce one embedding for each
# token in each sentence (useful for the output of the decoder).
# 
# The `RNN` module is composed of a stack of `RNNCell`. Each token embeddings
# coming out from a previous `RNNCell` is used as an input for the next `RNNCell` layer.
# 
# **Be careful !** Our `RNNCell` implementation is not exactly the same thing as
# the PyTorch's `nn.RNNCell`. PyTorch implements only the operations for one token
# (so you would need to loop through each tokens inside the `RNN` instead).
# You are free to implement `RNN` and `RNNCell` the way you want, as long as it has the expected behaviour of a RNN.
# 
# The same thing apply for the `GRU` and `GRUCell`.
# 

# In[ ]:


class RNNCell(nn.Module):
    """A single RNN layer.

    Parameters
    ----------
        input_size: Size of each input token.
        hidden_size: Size of each RNN hidden state.
        dropout: Dropout rate.

    Important note: This layer does not exactly the same thing as nn.RNNCell does.
    PyTorch implementation is only doing one simple pass over one token for each batch.
    This implementation is taking the whole sequence of each batch and provide the
    final hidden state along with the embeddings of each token in each sequence.
    """
    def __init__(
            self,
            input_size: int,
            hidden_size: int,
            dropout: float,
        ):
        super().__init__()

        self.rnn_cell = nn.Sequential(
            nn.Linear(input_size + hidden_size, hidden_size, device=DEVICE),
            nn.Tanh(),
            nn.Dropout(dropout)
        )

    def forward(self, x: torch.FloatTensor, h: torch.FloatTensor) -> tuple:
        """Go through all the sequence in x, iteratively updating
        the hidden state h.

        Args
        ----
            x: Input sequence.
                Shape of [batch_size, seq_len, input_size].
            h: Initial hidden state.
                Shape of [batch_size, hidden_size].

        Output
        ------
            y: Token embeddings.
                Shape of [batch_size, seq_len, hidden_size].
            h: Last hidden state.
                Shape of [batch_size, hidden_size].
        """
        #TODO

        y = torch.zeros(x.size(0), x.size(1), h.size(1), device=DEVICE)
        for t in range(x.size(1)):
            x_t = x[:, t]
            h = self.rnn_cell(torch.cat((x_t, h), dim=1))
            y[:,t] = h

        return y, h


class RNN(nn.Module):
    """Implementation of an RNN based
    on https://pytorch.org/docs/stable/generated/torch.nn.RNN.html.

    Parameters
    ----------
        input_size: Size of each input token.
        hidden_size: Size of each RNN hidden state.
        num_layers: Number of layers (RNNCell or GRUCell).
        dropout: Dropout rate.
        model_type: Either 'RNN' or 'GRU', to select which model we want.
            This parameter can be removed if you decide to use the module `GRU`.
            Indeed, `GRU` should have exactly the same code as this module,
            but with `GRUCell` instead of `RNNCell`. We let the freedom for you
            to decide at which level you want to specialise the modules (either
            in `TranslationRNN` by creating a `GRU` or a `RNN`, or in `RNN`
            by creating a `GRUCell` or a `RNNCell`).
    """
    def __init__(
            self,
            input_size: int,
            hidden_size: int,
            num_layers: int,
            dropout: float,
            model_type: str,
        ):
        super().__init__()

        # TODO
        if model_type == 'RNN':
            cell = RNNCell
        elif model_type == 'GRU':
            cell = GRUCell
        else :
          raise ValueError("Model type should be 'RNN' or 'GRU'.")

        self.input_size = input_size
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.model_type = model_type
        self.dropout = nn.Dropout(dropout)
        self.layers = nn.ModuleList([cell(input_size, hidden_size, dropout) if l==0 else cell(hidden_size, hidden_size, dropout) for l in range(num_layers)])


    def forward(self, x: torch.FloatTensor, h: torch.FloatTensor=None) -> tuple:
        """Pass the input sequence through all the RNN cells.
        Returns the output and the final hidden state of each RNN layer

        Args
        ----
            x: Input sequence.
                Shape of [batch_size, seq_len, input_size].
            h: Hidden state for each RNN layer.
                Can be None, in which case an initial hidden state is created.
                Shape of [batch_size, n_layers, hidden_size].

        Output
        ------
            y: Output embeddings for each token after the RNN layers.
                Shape of [batch_size, seq_len, hidden_size].
            h: Final hidden state.
                Shape of [batch_size, n_layers, hidden_size].
        """
        # TODO
        if h is None:
          h = torch.zeros(x.size(0), self.num_layers, self.hidden_size, device=DEVICE)

        y = torch.zeros(x.size(0), x.size(1), self.hidden_size, device=DEVICE)
        y = x
        #for layer, cell in enumerate(self.layers):
        for layer in range(self.num_layers):
            y = self.dropout(y)
            y, h[:, layer] = self.layers[layer](y, h[:, layer])
        return y, h


# ### GRU
# Here you have to implement a GRU-RNN. This architecture is close to the Vanilla RNN but perform different operations. Look up the pytorch documentation to figure out the differences.

# In[ ]:


# Pas besoin, nous avons géré cela dans la classe RNN, comme suggéré dans l'énoncé

# class GRU(nn.Module):
#     """Implementation of a GRU based on https://pytorch.org/docs/stable/generated/torch.nn.GRU.html.

#     Parameters
#     ----------
#         input_size: Size of each input token.
#         hidden_size: Size of each RNN hidden state.
#         num_layers: Number of layers.
#         dropout: Dropout rate.
#     """
#     def __init__(
#             self,
#             input_size: int,
#             hidden_size: int,
#             num_layers: int,
#             dropout: float,
#         ):
#         super().__init__()

#         # TODO
#

#     def forward(self, x: torch.FloatTensor, h: torch.FloatTensor=None) -> tuple:
#         """
#         Args
#         ----
#             x: Input sequence
#                 Shape of [batch_size, seq_len, input_size].
#             h: Initial hidden state for each layer.
#                 If 'None', then an initial hidden state (a zero filled tensor)
#                 is created.
#                 Shape of [batch_size, n_layers, hidden_size].

#         Output
#         ------
#             output:
#                 Shape of [batch_size, seq_len, hidden_size].
#             h_n: Final hidden state.
#                 Shape of [batch_size, n_layers, hidden size].
#         """
#         # TODO
#         pass


class GRUCell(nn.Module):
    """A single GRU layer.

    Parameters
    ----------
        input_size: Size of each input token.
        hidden_size: Size of each RNN hidden state.
        dropout: Dropout rate.
    """
    def __init__(
            self,
            input_size: int,
            hidden_size: int,
            dropout: float,
        ):
        super().__init__()
        # TODO
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.dropout = nn.Dropout(dropout)

        self.w_i = nn.Linear(input_size, 3 * hidden_size, bias=True, device=DEVICE)
        self.w_h = nn.Linear(hidden_size, 3 * hidden_size, bias=True, device=DEVICE)

    def forward(self, x: torch.FloatTensor, h: torch.FloatTensor) -> tuple:
        """
        Args
        ----
            x: Input sequence.
                Shape of [batch_size, seq_len, input_size].
            h: Initial hidden state.
                Shape of [batch_size, hidden_size].

        Output
        ------
            y: Token embeddings.
                Shape of [batch_size, seq_len, hidden_size].
            h: Last hidden state.
                Shape of [batch_size, hidden_size].
        """
        # TODO
        if h is None:
          h = torch.zeros(x.size(0), self.hidden_size, device=DEVICE)

        y = torch.zeros(x.size(0), x.size(1), h.size(1), device=DEVICE)

        for t in range(x.size(1)):
          x_t = x[:, t]
          gates = self.w_i(x_t) + self.w_h(h)
          update_gate, reset_gate, memory_content = gates.chunk(3, dim=1)

          update_gate = torch.sigmoid(update_gate)
          reset_gate = torch.sigmoid(reset_gate)
          memory_content = torch.tanh(memory_content)

          h_t = update_gate * h + (1 - update_gate) * (reset_gate * memory_content)
          h_t = self.dropout(h_t)

          y[:, t] = h_t
          h = h_t
        return y,h


# ### Translation RNN
# 
# This module instanciates a vanilla RNN or a GRU-RNN and performs the translation task. You have to:
# * Encode the source and target sequence
# * Pass the final hidden state of the encoder to the decoder (one for each layer)
# * Decode the hidden state into the target sequence
# 
# We use teacher forcing for training, meaning that when the next token is predicted, that prediction is based on the previous true target tokens.

# In[ ]:


class TranslationRNN(nn.Module):
    """Basic RNN encoder and decoder for a translation task.
    It can run as a vanilla RNN or a GRU-RNN.

    Parameters
    ----------
        n_tokens_src: Number of tokens in the source vocabulary.
        n_tokens_tgt: Number of tokens in the target vocabulary.
        dim_embedding: Dimension size of the word embeddings (for both language).
        dim_hidden: Dimension size of the hidden layers in the RNNs
            (for both the encoder and the decoder).
        n_layers: Number of layers in the RNNs.
        dropout: Dropout rate.
        src_pad_idx: Source padding index value.
        tgt_pad_idx: Target padding index value.
        model_type: Either 'RNN' or 'GRU', to select which model we want.
    """

    def __init__(
            self,
            n_tokens_src: int,
            n_tokens_tgt: int,
            dim_embedding: int,
            dim_hidden: int,
            n_layers: int,
            dropout: float,
            src_pad_idx: int,
            tgt_pad_idx: int,
            model_type: str,
        ):
        super().__init__()

        # TODO
        self.src_embedding = nn.Embedding(n_tokens_src, dim_embedding, padding_idx=src_pad_idx, device=DEVICE)
        self.tgt_embedding = nn.Embedding(n_tokens_tgt, dim_embedding, padding_idx=tgt_pad_idx, device=DEVICE)

        self.encoder = RNN(dim_embedding, dim_hidden, n_layers, dropout, model_type)
        self.decoder = RNN(dim_embedding, dim_hidden, n_layers, dropout, model_type)

        self.output_linear = nn.Linear(dim_hidden, n_tokens_tgt, device=DEVICE, bias = True)

    def forward(
        self,
        source: torch.LongTensor,
        target: torch.LongTensor
    ) -> torch.FloatTensor:
        """Predict the target tokens logites based on the source tokens.

        Args
        ----
            source: Batch of source sentences.
                Shape of [batch_size, src_seq_len].
            target: Batch of target sentences.
                Shape of [batch_size, tgt_seq_len].

        Output
        ------
            y: Distributions over the next token for all tokens in each sentences.
                Those need to be the logits only, do not apply a softmax because
                it will be done in the loss computation for numerical stability.
                See https://pytorch.org/docs/stable/generated/torch.nn.CrossEntropyLoss.html for more informations.
                Shape of [batch_size, tgt_seq_len, n_tokens_tgt].
        """
        # TODO
        batch_size = source.size(0)
        src_seq_len = source.size(1)
        tgt_seq_len = target.size(1)

        embedded_src = self.src_embedding(source)
        embedded_tgt = self.tgt_embedding(target)

        _, hidden_src = self.encoder(embedded_src)
        y, _ = self.decoder(embedded_tgt, hidden_src)
        y = self.output_linear(y)

        return y


# ## Transformer model
# Here you have to code the Transformer architecture.
# It is divided in three parts:
# * Attention layers
# * Encoder and decoder layers
# * Main layers (gather the encoder and decoder layers)
# 
# The [illustrated transformer](https://jalammar.github.io/illustrated-transformer/) blog can help you
# understanding how the architecture works.
# Once this is done, you can use [the annontated transformer](https://nlp.seas.harvard.edu/2018/04/03/attention.html) to have an idea of how to code this architecture.
# We encourage you to use `torch.einsum` and the `einops` library as much as you can. It will make your code simpler.
# 
# ---
# **Implementation order**
# 
# To help you with the implementation, we advise you following this order:
# * Implement `TranslationTransformer` and use `nn.Transformer` instead of `Transformer`
# * Implement `Transformer` and use `nn.TransformerDecoder` and `nn.TransformerEnocder`
# * Implement the `TransformerDecoder` and `TransformerEncoder` and use `nn.MultiHeadAttention`
# * Implement `MultiHeadAttention`
# 
# Do not forget to add `batch_first=True` when necessary in the `nn` modules.

# ### Attention layers
# We use a `MultiHeadAttention` module, that is able to perform self-attention aswell as cross-attention (depending on what you give as queries, keys and values).
# 
# **Attention**
# 
# 
# It takes the multiheaded queries, keys and values as input.
# It computes the attention between the queries and the keys and return the attended values.
# 
# The implementation of this function can greatly be improved with *einsums*.
# 
# **MultiheadAttention**
# 
# Computes the multihead queries, keys and values and feed them to the `attention` function.
# You also need to merge the key padding mask and the attention mask into one mask.
# 
# The implementation of this module can greatly be improved with *einops.rearrange*.

# In[ ]:


from einops.layers.torch import Rearrange
#from einops import rearrange

def attention(
        q: torch.FloatTensor,
        k: torch.FloatTensor,
        v: torch.FloatTensor,
        mask: torch.BoolTensor=None,
        dropout: nn.Dropout=None,
    ) -> tuple:
    """Computes multihead scaled dot-product attention from the
    projected queries, keys and values.

    Args
    ----
        q: Batch of queries.
            Shape of [batch_size, seq_len_1, n_heads, dim_model].
        k: Batch of keys.
            Shape of [batch_size, seq_len_2, n_heads, dim_model].
        v: Batch of values.
            Shape of [batch_size, seq_len_2, n_heads, dim_model].
        mask: Prevent tokens to attend to some other tokens (for padding or autoregressive attention).
            Attention is prevented where the mask is `True`.
            Shape of [batch_size, n_heads, seq_len_1, seq_len_2],
            or broadcastable to that shape.
        dropout: Dropout layer to use.

    Output
    ------
        y: Multihead scaled dot-attention between the queries, keys and values.
            Shape of [batch_size, seq_len_1, n_heads, dim_model].
        attn: Computed attention between the keys and the queries.
            Shape of [batch_size, n_heads, seq_len_1, seq_len_2].
    """
    # TODO
    attn = torch.einsum('bqhd,bkhd->bhqk', q, k) / np.sqrt(q.shape[-1])
    if mask is not None:
        attn.masked_fill_(mask, float('-inf'))

    attn = torch.softmax(attn, dim=-1)
    if dropout is not None:
        attn = dropout(attn)

    y = torch.einsum('bhqk,bkhd->bqhd', attn, v)
    return y, attn

class MultiheadAttention(nn.Module):
    """Multihead attention module.
    Can be used as a self-attention and cross-attention layer.
    The queries, keys and values are projected into multiple heads
    before computing the attention between those tensors.

    Parameters
    ----------
        dim: Dimension of the input tokens.
        n_heads: Number of heads. `dim` must be divisible by `n_heads`.
        dropout: Dropout rate.
    """
    def __init__(
            self,
            dim: int,
            n_heads: int,
            dropout: float,
        ):
        super().__init__()

        assert dim % n_heads == 0

        # TODO
        super().__init__()
        self.n_heads = n_heads
        self.dim_head = dim // n_heads

        self.qkv = nn.Linear(dim, dim * 3)
        self.rearrange_qkv = Rearrange('b t (qkv h d) -> qkv b t h d', qkv=3, h=n_heads)
        self.unify_heads = nn.Linear(dim, dim)

        self.dropout = nn.Dropout(dropout) if dropout > 0 else None

    def forward(
            self,
            q: torch.FloatTensor,
            k: torch.FloatTensor,
            v: torch.FloatTensor,
            key_padding_mask: torch.BoolTensor = None,
            attn_mask: torch.BoolTensor = None,
        ) -> torch.FloatTensor:
        """Computes the scaled multi-head attention form the input queries,
        keys and values.

        Project those queries, keys and values before feeding them
        to the `attention` function.

        The masks are boolean masks. Tokens are prevented to attends to
        positions where the mask is `True`.

        Args
        ----
            q: Batch of queries.
                Shape of [batch_size, seq_len_1, dim_model].
            k: Batch of keys.
                Shape of [batch_size, seq_len_2, dim_model].
            v: Batch of values.
                Shape of [batch_size, seq_len_2, dim_model].
            key_padding_mask: Prevent attending to padding tokens.
                Shape of [batch_size, seq_len_2].
            attn_mask: Prevent attending to subsequent tokens.
                Shape of [seq_len_1, seq_len_2].

        Output
        ------
            y: Computed multihead attention.
                Shape of [batch_size, seq_len_1, dim_model].
        """
        # TODO
        batch_size, seq_len_1, _ = q.size()
        _, seq_len_2, _ = k.size()

        qkv = self.qkv(torch.cat((q, k, v), dim=1))
        q, k, v = self.rearrange_qkv(qkv).chunk(3, dim=0)

        if key_padding_mask is not None:
            key_padding_mask = key_padding_mask.unsqueeze(1).unsqueeze(2)
            key_padding_mask = key_padding_mask.expand(batch_size, self.n_heads, -1, seq_len_2)

        if attn_mask is not None:
            attn_mask = attn_mask.unsqueeze(0).unsqueeze(1)
            attn_mask = attn_mask.expand(batch_size, self.n_heads, *attn_mask.shape[-2:])

        mask = key_padding_mask | attn_mask if key_padding_mask is not None or attn_mask is not None else None

        y, _ = attention(q, k, v, mask, dropout=self.dropout)
        y = y.reshape(batch_size, seq_len_1, -1)
        y = self.unify_heads(y)

        return y


# ### Encoder and decoder layers
# 
# **TranformerEncoder**
# 
# Apply self-attention layers onto the source tokens.
# It only needs the source key padding mask.
# 
# 
# **TranformerDecoder**
# 
# Apply masked self-attention layers to the target tokens and cross-attention
# layers between the source and the target tokens.
# It needs the source and target key padding masks, and the target attention mask.

# In[ ]:


class TransformerDecoderLayer(nn.Module):
    """Single decoder layer.

    Parameters
    ----------
        d_model: The dimension of decoders inputs/outputs.
        dim_feedforward: Hidden dimension of the feedforward networks.
        nheads: Number of heads for each multi-head attention.
        dropout: Dropout rate.
    """

    def __init__(
            self,
            d_model: int,
            d_ff: int,
            nhead: int,
            dropout: float
        ):
        super().__init__()

        # TODO
        self.self_attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout)
        self.multihead_attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout)
        self.dropout = nn.Dropout(dropout)
        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)
        self.norm3 = nn.LayerNorm(d_model)
        self.feed_forward = nn.Sequential(
            nn.Linear(d_model, d_ff),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(d_ff, d_model),
        )


    def forward(
            self,
            src: torch.FloatTensor,
            tgt: torch.FloatTensor,
            tgt_mask_attn: torch.BoolTensor,
            src_key_padding_mask: torch.BoolTensor,
            tgt_key_padding_mask: torch.BoolTensor,
        ) -> torch.FloatTensor:
        """Decode the next target tokens based on the previous tokens.

        Args
        ----
            src: Batch of source sentences.
                Shape of [batch_size, src_seq_len, dim_model].
            tgt: Batch of target sentences.
                Shape of [batch_size, tgt_seq_len, dim_model].
            tgt_mask_attn: Mask to prevent attention to subsequent tokens.
                Shape of [tgt_seq_len, tgt_seq_len].
            src_key_padding_mask: Mask to prevent attention to padding in src sequence.
                Shape of [batch_size, src_seq_len].
            tgt_key_padding_mask: Mask to prevent attention to padding in tgt sequence.
                Shape of [batch_size, tgt_seq_len].

        Output
        ------
            y:  Batch of sequence of embeddings representing the predicted target tokens
                Shape of [batch_size, tgt_seq_len, dim_model].
        """
        # TODO
        tgt2, _ = self.self_attn(tgt, tgt, tgt, attn_mask=tgt_mask_attn, key_padding_mask=tgt_key_padding_mask)
        tgt = tgt + self.dropout(tgt2)
        tgt = self.norm1(tgt)

        tgt2, _ = self.multihead_attn(tgt, src, src, key_padding_mask=src_key_padding_mask)
        tgt = tgt + self.dropout(tgt2)
        tgt = self.norm2(tgt)

        tgt2 = self.feed_forward(tgt)
        tgt = tgt + self.dropout(tgt2)
        tgt = self.norm3(tgt)

        return tgt

class TransformerDecoder(nn.Module):
    """Implementation of the transformer decoder stack.

    Parameters
    ----------
        d_model: The dimension of decoders inputs/outputs.
        dim_feedforward: Hidden dimension of the feedforward networks.
        num_decoder_layers: Number of stacked decoders.
        nheads: Number of heads for each multi-head attention.
        dropout: Dropout rate.
    """

    def __init__(
            self,
            d_model: int,
            d_ff: int,
            num_decoder_layer:int ,
            nhead: int,
            dropout: float
        ):
        super().__init__()

        self.layers = nn.ModuleList([
            TransformerDecoderLayer(d_model, d_ff, nhead, dropout)
            for _ in range(num_decoder_layer)
        ])


    def forward(
            self,
            src: torch.FloatTensor,
            tgt: torch.FloatTensor,
            tgt_mask_attn: torch.BoolTensor,
            src_key_padding_mask: torch.BoolTensor,
            tgt_key_padding_mask: torch.BoolTensor,
        ) -> torch.FloatTensor:
        """Decodes the source sequence by sequentially passing.
        the encoded source sequence and the target sequence through the decoder stack.

        Args
        ----
            src: Batch of encoded source sentences.
                Shape of [batch_size, src_seq_len, dim_model].
            tgt: Batch of taget sentences.
                Shape of [batch_size, tgt_seq_len, dim_model].
            tgt_mask_attn: Mask to prevent attention to subsequent tokens.
                Shape of [tgt_seq_len, tgt_seq_len].
            src_key_padding_mask: Mask to prevent attention to padding in src sequence.
                Shape of [batch_size, src_seq_len].
            tgt_key_padding_mask: Mask to prevent attention to padding in tgt sequence.
                Shape of [batch_size, tgt_seq_len].

        Output
        ------
            y:  Batch of sequence of embeddings representing the predicted target tokens
                Shape of [batch_size, tgt_seq_len, dim_model].
        """

        y = tgt
        for layer in self.layers:
            y = layer(src, y, tgt_mask_attn, src_key_padding_mask, tgt_key_padding_mask)
        return y


class TransformerEncoderLayer(nn.Module):
    """Single encoder layer.

    Parameters
    ----------
        d_model: The dimension of input tokens.
        dim_feedforward: Hidden dimension of the feedforward networks.
        nheads: Number of heads for each multi-head attention.
        dropout: Dropout rate.
    """

    def __init__(
            self,
            d_model: int,
            d_ff: int,
            nhead: int,
            dropout: float,
        ):
        super().__init__()

        self.self_attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout)
        self.dropout1 = nn.Dropout(dropout)
        self.norm1 = nn.LayerNorm(d_model)
        self.dropout2 = nn.Dropout(dropout)
        self.norm2 = nn.LayerNorm(d_model)
        self.feed_forward = nn.Sequential(
            nn.Linear(d_model, d_ff),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(d_ff, d_model),
        )

    def forward(
        self,
        src: torch.FloatTensor,
        key_padding_mask: torch.BoolTensor
        ) -> torch.FloatTensor:
        """Encodes the input. Does not attend to masked inputs.

        Args
        ----
            src: Batch of embedded source tokens.
                Shape of [batch_size, src_seq_len, dim_model].
            key_padding_mask: Mask preventing attention to padding tokens.
                Shape of [batch_size, src_seq_len].

        Output
        ------
            y: Batch of encoded source tokens.
                Shape of [batch_size, src_seq_len, dim_model].
        """

        src2, _ = self.self_attn(src, src, src, key_padding_mask=key_padding_mask)
        src = src + self.dropout1(src2)
        src = self.norm1(src)

        src2 = self.feed_forward(src)
        src = src + self.dropout2(src2)
        src = self.norm2(src)

        return src


class TransformerEncoder(nn.Module):
    """Implementation of the transformer encoder stack.

    Parameters
    ----------
        d_model: The dimension of encoders inputs.
        dim_feedforward: Hidden dimension of the feedforward networks.
        num_encoder_layers: Number of stacked encoders.
        nheads: Number of heads for each multi-head attention.
        dropout: Dropout rate.
    """

    def __init__(
            self,
            d_model: int,
            dim_feedforward: int,
            num_encoder_layers: int,
            nheads: int,
            dropout: float
        ):
        super().__init__()

        self.layers = nn.ModuleList([
            TransformerEncoderLayer(d_model, dim_feedforward,nheads, dropout=dropout)
            for _ in range(num_encoder_layers)
        ])

    def forward(
            self,
            src: torch.FloatTensor,
            key_padding_mask: torch.BoolTensor
        ) -> torch.FloatTensor:
        """Encodes the source sequence by sequentially passing.
        the source sequence through the encoder stack.

        Args
        ----
            src: Batch of embedded source sentences.
                Shape of [batch_size, src_seq_len, dim_model].
            key_padding_mask: Mask preventing attention to padding tokens.
                Shape of [batch_size, src_seq_len].

        Output
        ------
            y: Batch of encoded source sequence.
                Shape of [batch_size, src_seq_len, dim_model].
        """
        y = src
        for layer in self.layers:
            y = layer(y, key_padding_mask=key_padding_mask)
        return y


# ### Main layers
# This section gather the `Transformer` and the `TranslationTransformer` modules.
# 
# **Transformer**
# 
# 
# The classical transformer architecture.
# It takes the source and target tokens embeddings and
# do the forward pass through the encoder and decoder.
# 
# **Translation Transformer**
# 
# Compute the source and target tokens embeddings, and apply a final head to produce next token logits.
# The output must not be the softmax but just the logits, because we use the `nn.CrossEntropyLoss`.
# 
# It also creates the *src_key_padding_mask*, the *tgt_key_padding_mask* and the *tgt_mask_attn*.

# In[ ]:


class Transformer(nn.Module):
    """Implementation of a Transformer based on the paper: https://arxiv.org/pdf/1706.03762.pdf.

    Parameters
    ----------
        d_model: The dimension of encoders/decoders inputs/ouputs.
        nhead: Number of heads for each multi-head attention.
        num_encoder_layers: Number of stacked encoders.
        num_decoder_layers: Number of stacked encoders.
        dim_feedforward: Hidden dimension of the feedforward networks.
        dropout: Dropout rate.
    """

    def __init__(
            self,
            d_model: int,
            nhead: int,
            num_encoder_layers: int,
            num_decoder_layers: int,
            dim_feedforward: int,
            dropout: float,
        ):
        super().__init__()
        # TODO
        encoder_layer = TransformerEncoderLayer(d_model=d_model, nhead=nhead, d_ff=dim_feedforward, dropout=dropout)
        decoder_layer = TransformerDecoderLayer(d_model=d_model, nhead=nhead, d_ff=dim_feedforward, dropout=dropout)

        self.encoder = TransformerEncoder(d_model=d_model, nheads=nhead, dim_feedforward=dim_feedforward, dropout=dropout, num_encoder_layers=num_encoder_layers)
        self.decoder = TransformerDecoder(d_model=d_model, nhead=nhead, d_ff=dim_feedforward, dropout=dropout, num_decoder_layer=num_decoder_layers)


    def forward(
            self,
            src: torch.FloatTensor,
            tgt: torch.FloatTensor,
            tgt_mask_attn: torch.BoolTensor,
            src_key_padding_mask: torch.BoolTensor,
            tgt_key_padding_mask: torch.BoolTensor
        ) -> torch.FloatTensor:
        """Compute next token embeddings.

        Args
        ----
            src: Batch of source sequences.
                Shape of [batch_size, src_seq_len, dim_model].
            tgt: Batch of target sequences.
                Shape of [batch_size, tgt_seq_len, dim_model].
            tgt_mask_attn: Mask to prevent attention to subsequent tokens.
                Shape of [tgt_seq_len, tgt_seq_len].
            src_key_padding_mask: Mask to prevent attention to padding in src sequence.
                Shape of [batch_size, src_seq_len].
            tgt_key_padding_mask: Mask to prevent attention to padding in tgt sequence.
                Shape of [batch_size, tgt_seq_len].

        Output
        ------
            y: Next token embeddings, given the previous target tokens and the source tokens.
                Shape of [batch_size, tgt_seq_len, dim_model].
        """
        # TODO
        encoded_src = self.encoder(src, key_padding_mask=src_key_padding_mask)
        decoded_tgt = self.decoder(src=encoded_src, tgt=tgt, tgt_mask_attn=tgt_mask_attn, src_key_padding_mask=src_key_padding_mask, tgt_key_padding_mask=tgt_key_padding_mask)
        return decoded_tgt


class TranslationTransformer(nn.Module):
    """Basic Transformer encoder and decoder for a translation task.
    Manage the masks creation, and the token embeddings.
    Position embeddings can be learnt with a standard `nn.Embedding` layer.

    Parameters
    ----------
        n_tokens_src: Number of tokens in the source vocabulary.
        n_tokens_tgt: Number of tokens in the target vocabulary.
        n_heads: Number of heads for each multi-head attention.
        dim_embedding: Dimension size of the word embeddings (for both language).
        dim_hidden: Dimension size of the feedforward layers
            (for both the encoder and the decoder).
        n_layers: Number of layers in the encoder and decoder.
        dropout: Dropout rate.
        src_pad_idx: Source padding index value.
        tgt_pad_idx: Target padding index value.
    """
    def __init__(
            self,
            n_tokens_src: int,
            n_tokens_tgt: int,
            n_heads: int,
            dim_embedding: int,
            dim_hidden: int,
            n_layers: int,
            dropout: float,
            src_pad_idx: int,
            tgt_pad_idx: int,
        ):
        super().__init__()

        # TODO
        #self.src_embedding = nn.Embedding(n_tokens_src, dim_embedding)
        #self.tgt_embedding = nn.Embedding(n_tokens_tgt, dim_embedding)
        #self.transformer = Transformer(dim_embedding, n_heads, n_layers, n_layers, dim_hidden, dropout)
        #self.position_encoding = positional_encoding(dim_embedding)
        #self.fc_out = nn.Linear(dim_embedding, n_tokens_tgt)
        #self.src_pad_idx = src_pad_idx
        #self.tgt_pad_idx = tgt_pad_idx

        self.src_pad_idx = src_pad_idx
        self.tgt_pad_idx = tgt_pad_idx

        self.src_embedding = nn.Embedding(n_tokens_src, dim_embedding, padding_idx=src_pad_idx)
        self.tgt_embedding = nn.Embedding(n_tokens_tgt, dim_embedding, padding_idx=tgt_pad_idx)

        self.src_position_embedding = nn.Embedding(256, dim_embedding)
        self.tgt_position_embedding = nn.Embedding(256, dim_embedding)

        self.transformer = Transformer(dim_embedding, n_heads, n_layers, n_layers, dim_hidden, dropout)
        self.output_layer = nn.Linear(dim_embedding, n_tokens_tgt)

    def forward(
            self,
            source: torch.LongTensor,
            target: torch.LongTensor
        ) -> torch.FloatTensor:
        """Predict the target tokens logites based on the source tokens.

        Args
        ----
            source: Batch of source sentences.
                Shape of [batch_size, seq_len_src].
            target: Batch of target sentences.
                Shape of [batch_size, seq_len_tgt].

        Output
        ------
            y: Distributions over the next token for all tokens in each sentences.
                Those need to be the logits only, do not apply a softmax because
                it will be done in the loss computation for numerical stability.
                See https://pytorch.org/docs/stable/generated/torch.nn.CrossEntropyLoss.html for more informations.
                Shape of [batch_size, seq_len_tgt, n_tokens_tgt].
        """
        # TODO
        src_seq_len = source.shape[1]
        tgt_seq_len = target.shape[1]


        src_positions = (
            torch.arange(0, src_seq_len).unsqueeze(0).repeat(source.shape[0], 1).to(source.device)
        )
        tgt_positions = (
            torch.arange(0, tgt_seq_len).unsqueeze(0).repeat(target.shape[0], 1).to(target.device)
        )


        src_key_padding_mask = (source == self.src_pad_idx)
        tgt_key_padding_mask = (target == self.tgt_pad_idx)

        #tgt_mask_attn = torch.triu(torch.ones((tgt_seq_len, tgt_seq_len), device=DEVICE)).bool()
        tgt_mask_attn = torch.ones(tgt_seq_len, tgt_seq_len, dtype = torch.bool, device = DEVICE)
        tgt_mask_attn = torch.triu(tgt_mask_attn, diagonal = 1)

        src_emb = self.src_embedding(source) + self.src_position_embedding(src_positions)
        tgt_emb = self.tgt_embedding(target) + self.tgt_position_embedding(tgt_positions)

        src_emb = src_emb.permute(1, 0, 2)
        tgt_emb = tgt_emb.permute(1, 0, 2)

        # logits = self.transformer(
        #     src_emb, tgt_emb, tgt_mask_attn, src_key_padding_mask, tgt_key_padding_mask
        # )
        logits = self.transformer(src_emb, tgt_emb, tgt_mask_attn=tgt_mask_attn, src_key_padding_mask=src_key_padding_mask, tgt_key_padding_mask=tgt_key_padding_mask)

        output = self.output_layer(logits.permute(1, 0, 2))
        return output
        # src_emb = self.src_embedding(source)
        # tgt_emb = self.tgt_embedding(target)


        # logits = self.transformer(
        #     src_emb, tgt_emb, tgt_mask_attn, src_key_padding_mask, tgt_key_padding_mask
        # )
        # #output = self.fc_out(logits)
        # output = self.output_layer(logits)
        # return output


# # Greedy search
# 
# Here you have to implement a geedy search to generate a target translation from a trained model and an input source string.
# The next token will simply be the most probable one.

# In[ ]:


def greedy_search(
        model: nn.Module,
        source: str,
        src_vocab: Vocab,
        tgt_vocab: Vocab,
        src_tokenizer,
        device: str,
        max_sentence_length: int,
    ) -> str:
    """Do a beam search to produce probable translations.

    Args
    ----
        model: The translation model. Assumes it produces logits score (before softmax).
        source: The sentence to translate.
        src_vocab: The source vocabulary.
        tgt_vocab: The target vocabulary.
        device: Device to which we make the inference.
        max_target: Maximum number of target sentences we keep at the end of each stage.
        max_sentence_length: Maximum number of tokens for the translated sentence.

    Output
    ------
        sentence: The translated source sentence.
    """
    model.eval()
    tokens = [src_vocab[token] for token in src_tokenizer(source)]
    src_tensor = torch.LongTensor(tokens).unsqueeze(0).to(device)
    tgt_tensor = torch.LongTensor([tgt_vocab.stoi[tgt_vocab.init_token]]).unsqueeze(0).to(device)

    for _ in range(max_sentence_length):
        with torch.no_grad():
            logits = model(src_tensor, tgt_tensor)
            next_token_idx = logits.argmax(2)[:, -1].item()

        tgt_tensor = torch.cat([tgt_tensor, torch.LongTensor([[next_token_idx]]).to(device)], dim=1)

        if next_token_idx == tgt_vocab.stoi[tgt_vocab.eos_token]:
            break

    translated_sentence = ' '.join(tgt_vocab.itos[idx] for idx in tgt_tensor[0][1:].tolist())

    return translated_sentence


# # Beam search
# Beam search is a smarter way of producing a sequence of tokens from
# an autoregressive model than just using a greedy search.
# 
# The greedy search always choose the most probable token as the unique
# and only next target token, and repeat this processus until the *\<eos\>* token is predicted.
# 
# Instead, the beam search selects the k-most probable tokens at each step.
# From those k tokens, the current sequence is duplicated k times and the k tokens are appended to the k sequences to produce new k sequences.
# 
# *You don't have to understand this code, but understanding this code once the TP is over could improve your torch tensors skills.*
# 
# ---
# 
# **More explanations**
# 
# Since it is done at each step, the number of sequences grows exponentially (k sequences after the first step, k² sequences after the second...).
# In order to keep the number of sequences low, we remove sequences except the top-s most likely sequences.
# To do that, we keep track of the likelihood of each sequence.
# 
# Formally, we define $s = [s_1, ..., s_{N_s}]$ as the source sequence made of $N_s$ tokens.
# We also define $t^i = [t_1, ..., t_i]$ as the target sequence at the beginning of the step $i$.
# 
# The output of the model parameterized by $\theta$ is:
# 
# $$
# T_{i+1} = p(t_{i+1} | s, t^i ; \theta )
# $$
# 
# Where $T_{i+1}$ is the distribution of the next token $t_{i+1}$.
# 
# Then, we define the likelihood of a target sentence $t = [t_1, ..., t_{N_t}]$ as:
# 
# $$
# L(t) = \prod_{i=1}^{N_t - 1} p(t_{i+1} | s, t_{i}; \theta )
# $$
# 
# Pseudocode of the beam search:
# ```
# source: [N_s source tokens]  # Shape of [total_source_tokens]
# target: [1, <bos> token]  # Shape of [n_sentences, current_target_tokens]
# target_prob: [1]  # Shape of [n_sentences]
# # We use `n_sentences` as the batch_size dimension
# 
# while current_target_tokens <= max_target_length:
#     source = repeat(source, n_sentences)  # Shape of [n_sentences, total_source_tokens]
#     predicted = model(source, target)[:, -1]  # Predict the next token distributions of all the n_sentences
#     tokens_idx, tokens_prob = topk(predicted, k)
# 
#     # Append the `n_sentences * k` tokens to the `n_sentences` sentences
#     target = repeat(target, k)  # Shape of [n_sentences * k, current_target_tokens]
#     target = append_tokens(target, tokens_idx)  # Shape of [n_sentences * k, current_target_tokens + 1]
# 
#     # Update the sentences probabilities
#     target_prob = repeat(target_prob, k)  # Shape of [n_sentences * k]
#     target_prob *= tokens_prob
# 
#     if n_sentences * k >= max_sentences:
#         target, target_prob = topk_prob(target, target_prob, k=max_sentences)
#     else:
#         n_sentences *= k
# 
#     current_target_tokens += 1
# ```

# In[ ]:


def beautify(sentence: str) -> str:
    """Removes useless spaces.
    """
    punc = {'.', ',', ';'}
    for p in punc:
        sentence = sentence.replace(f' {p}', p)

    links = {'-', "'"}
    for l in links:
        sentence = sentence.replace(f'{l} ', l)
        sentence = sentence.replace(f' {l}', l)

    return sentence


# In[ ]:


def indices_terminated(
        target: torch.FloatTensor,
        eos_token: int
    ) -> tuple:
    """Split the target sentences between the terminated and the non-terminated
    sentence. Return the indices of those two groups.

    Args
    ----
        target: The sentences.
            Shape of [batch_size, n_tokens].
        eos_token: Value of the End-of-Sentence token.

    Output
    ------
        terminated: Indices of the terminated sentences (who's got the eos_token).
            Shape of [n_terminated, ].
        non-terminated: Indices of the unfinished sentences.
            Shape of [batch_size-n_terminated, ].
    """
    terminated = [i for i, t in enumerate(target) if eos_token in t]
    non_terminated = [i for i, t in enumerate(target) if eos_token not in t]
    return torch.LongTensor(terminated), torch.LongTensor(non_terminated)


def append_beams(
        target: torch.FloatTensor,
        beams: torch.FloatTensor
    ) -> torch.FloatTensor:
    """Add the beam tokens to the current sentences.
    Duplicate the sentences so one token is added per beam per batch.

    Args
    ----
        target: Batch of unfinished sentences.
            Shape of [batch_size, n_tokens].
        beams: Batch of beams for each sentences.
            Shape of [batch_size, n_beams].

    Output
    ------
        target: Batch of sentences with one beam per sentence.
            Shape of [batch_size * n_beams, n_tokens+1].
    """
    batch_size, n_beams = beams.shape
    n_tokens = target.shape[1]

    target = einops.repeat(target, 'b t -> b c t', c=n_beams)  # [batch_size, n_beams, n_tokens]
    beams = beams.unsqueeze(dim=2)  # [batch_size, n_beams, 1]

    target = torch.cat((target, beams), dim=2)  # [batch_size, n_beams, n_tokens+1]
    target = target.view(batch_size*n_beams, n_tokens+1)  # [batch_size * n_beams, n_tokens+1]
    return target


def beam_search(
        model: nn.Module,
        source: str,
        src_vocab: Vocab,
        tgt_vocab: Vocab,
        src_tokenizer,
        device: str,
        beam_width: int,
        max_target: int,
        max_sentence_length: int,
    ) -> list:
    """Do a beam search to produce probable translations.

    Args
    ----
        model: The translation model. Assumes it produces linear score (before softmax).
        source: The sentence to translate.
        src_vocab: The source vocabulary.
        tgt_vocab: The target vocabulary.
        device: Device to which we make the inference.
        beam_width: Number of top-k tokens we keep at each stage.
        max_target: Maximum number of target sentences we keep at the end of each stage.
        max_sentence_length: Maximum number of tokens for the translated sentence.

    Output
    ------
        sentences: List of sentences orderer by their likelihood.
    """
    src_tokens = ['<bos>'] + src_tokenizer(source) + ['<eos>']
    src_tokens = src_vocab(src_tokens)

    tgt_tokens = ['<bos>']
    tgt_tokens = tgt_vocab(tgt_tokens)

    # To tensor and add unitary batch dimension
    src_tokens = torch.LongTensor(src_tokens).to(device)
    tgt_tokens = torch.LongTensor(tgt_tokens).unsqueeze(dim=0).to(device)
    target_probs = torch.FloatTensor([1]).to(device)
    model.to(device)

    EOS_IDX = tgt_vocab['<eos>']
    with torch.no_grad():
        while tgt_tokens.shape[1] < max_sentence_length:
            batch_size, n_tokens = tgt_tokens.shape

            # Get next beams
            src = einops.repeat(src_tokens, 't -> b t', b=tgt_tokens.shape[0])
            predicted = model.forward(src, tgt_tokens)
            predicted = torch.softmax(predicted, dim=-1)
            probs, predicted = predicted[:, -1].topk(k=beam_width, dim=-1)

            # Separe between terminated sentences and the others
            idx_terminated, idx_not_terminated = indices_terminated(tgt_tokens, EOS_IDX)
            idx_terminated, idx_not_terminated = idx_terminated.to(device), idx_not_terminated.to(device)

            tgt_terminated = torch.index_select(tgt_tokens, dim=0, index=idx_terminated)
            tgt_probs_terminated = torch.index_select(target_probs, dim=0, index=idx_terminated)

            filter_t = lambda t: torch.index_select(t, dim=0, index=idx_not_terminated)
            tgt_others = filter_t(tgt_tokens)
            tgt_probs_others = filter_t(target_probs)
            predicted = filter_t(predicted)
            probs = filter_t(probs)

            # Add the top tokens to the previous target sentences
            tgt_others = append_beams(tgt_others, predicted)

            # Add padding to terminated target
            padd = torch.zeros((len(tgt_terminated), 1), dtype=torch.long, device=device)
            tgt_terminated = torch.cat(
                (tgt_terminated, padd),
                dim=1
            )

            # Update each target sentence probabilities
            tgt_probs_others = torch.repeat_interleave(tgt_probs_others, beam_width)
            tgt_probs_others *= probs.flatten()
            tgt_probs_terminated *= 0.999  # Penalize short sequences overtime

            # Group up the terminated and the others
            target_probs = torch.cat(
                (tgt_probs_others, tgt_probs_terminated),
                dim=0
            )
            tgt_tokens = torch.cat(
                (tgt_others, tgt_terminated),
                dim=0
            )

            # Keep only the top `max_target` target sentences
            if target_probs.shape[0] <= max_target:
                continue

            target_probs, indices = target_probs.topk(k=max_target, dim=0)
            tgt_tokens = torch.index_select(tgt_tokens, dim=0, index=indices)

    sentences = []
    for tgt_sentence in tgt_tokens:
        tgt_sentence = list(tgt_sentence)[1:]  # Remove <bos> token
        tgt_sentence = list(takewhile(lambda t: t != EOS_IDX, tgt_sentence))
        tgt_sentence = ' '.join(tgt_vocab.lookup_tokens(tgt_sentence))
        sentences.append(tgt_sentence)

    sentences = [beautify(s) for s in sentences]

    # Join the sentences with their likelihood
    sentences = [(s, p.item()) for s, p in zip(sentences, target_probs)]
    # Sort the sentences by their likelihood
    sentences = [(s, p) for s, p in sorted(sentences, key=lambda k: k[1], reverse=True)]

    return sentences


# # Training loop
# This is a basic training loop code. It takes a big configuration dictionnary to avoid never ending arguments in the functions.
# We use [Weights and Biases](https://wandb.ai/) to log the trainings.
# It logs every training informations and model performances in the cloud.
# You have to create an account to use it. Every accounts are free for individuals or research teams.

# In[ ]:


def print_logs(dataset_type: str, logs: dict):
    """Print the logs.

    Args
    ----
        dataset_type: Either "Train", "Eval", "Test" type.
        logs: Containing the metric's name and value.
    """
    desc = [
        f'{name}: {value:.2f}'
        for name, value in logs.items()
    ]
    desc = '\t'.join(desc)
    desc = f'{dataset_type} -\t' + desc
    desc = desc.expandtabs(5)
    print(desc)


def topk_accuracy(
        real_tokens: torch.FloatTensor,
        probs_tokens: torch.FloatTensor,
        k: int,
        tgt_pad_idx: int,
    ) -> torch.FloatTensor:
    """Compute the top-k accuracy.
    We ignore the PAD tokens.

    Args
    ----
        real_tokens: Real tokens of the target sentence.
            Shape of [batch_size * n_tokens].
        probs_tokens: Tokens probability predicted by the model.
            Shape of [batch_size * n_tokens, n_target_vocabulary].
        k: Top-k accuracy threshold.
        src_pad_idx: Source padding index value.

    Output
    ------
        acc: Scalar top-k accuracy value.
    """
    total = (real_tokens != tgt_pad_idx).sum()

    _, pred_tokens = probs_tokens.topk(k=k, dim=-1)  # [batch_size * n_tokens, k]
    real_tokens = einops.repeat(real_tokens, 'b -> b k', k=k)  # [batch_size * n_tokens, k]

    good = (pred_tokens == real_tokens) & (real_tokens != tgt_pad_idx)
    acc = good.sum() / total
    return acc


def loss_batch(
        model: nn.Module,
        source: torch.LongTensor,
        target: torch.LongTensor,
        config: dict,
    )-> dict:
    """Compute the metrics associated with this batch.
    The metrics are:
        - loss
        - top-1 accuracy
        - top-5 accuracy
        - top-10 accuracy

    Args
    ----
        model: The model to train.
        source: Batch of source tokens.
            Shape of [batch_size, n_src_tokens].
        target: Batch of target tokens.
            Shape of [batch_size, n_tgt_tokens].
        config: Additional parameters.

    Output
    ------
        metrics: Dictionnary containing evaluated metrics on this batch.
    """
    device = config['device']
    loss_fn = config['loss'].to(device)
    metrics = dict()

    source, target = source.to(device), target.to(device)
    target_in, target_out = target[:, :-1], target[:, 1:]

    # Loss
    pred = model(source, target_in)  # [batch_size, n_tgt_tokens-1, n_vocab]
    pred = pred.view(-1, pred.shape[2])  # [batch_size * (n_tgt_tokens - 1), n_vocab]
    target_out = target_out.flatten()  # [batch_size * (n_tgt_tokens - 1),]
    print(pred.shape, target_out.shape)
    metrics['loss'] = loss_fn(pred, target_out)

    # Accuracy - we ignore the padding predictions
    for k in [1, 5, 10]:
        metrics[f'top-{k}'] = topk_accuracy(target_out, pred, k, config['tgt_pad_idx'])

    return metrics


def eval_model(model: nn.Module, dataloader: DataLoader, config: dict) -> dict:
    """Evaluate the model on the given dataloader.
    """
    device = config['device']
    logs = defaultdict(list)

    model.to(device)
    model.eval()

    with torch.no_grad():
        for source, target in dataloader:
            metrics = loss_batch(model, source, target, config)
            for name, value in metrics.items():
                logs[name].append(value.cpu().item())

    for name, values in logs.items():
        logs[name] = np.mean(values)
    return logs


def train_model(model: nn.Module, config: dict):
    """Train the model in a teacher forcing manner.
    """
    torch.autograd.set_detect_anomaly(False)
    train_loader, val_loader = config['train_loader'], config['val_loader']
    train_dataset, val_dataset = train_loader.dataset.dataset, val_loader.dataset.dataset
    optimizer = config['optimizer']
    clip = config['clip']
    device = config['device']

    columns = ['epoch']
    for mode in ['train', 'validation']:
        columns += [
            f'{mode} - {colname}'
            for colname in ['source', 'target', 'predicted', 'likelihood']
        ]
    log_table = wandb.Table(columns=columns)


    print(f'Starting training for {config["epochs"]} epochs, using {device}.')
    for e in range(config['epochs']):
        print(f'\nEpoch {e+1}')

        model.to(device)
        model.train()
        logs = defaultdict(list)

        for batch_id, (source, target) in enumerate(train_loader):
            optimizer.zero_grad()

            metrics = loss_batch(model, source, target, config)
            loss = metrics['loss']


            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), clip)
            optimizer.step()

            for name, value in metrics.items():
                logs[name].append(value.cpu().item())  # Don't forget the '.item' to free the cuda memory

            if batch_id % config['log_every'] == 0:
                for name, value in logs.items():
                    logs[name] = np.mean(value)

                train_logs = {
                    f'Train - {m}': v
                    for m, v in logs.items()
                }
                wandb.log(train_logs)
                logs = defaultdict(list)

        # Logs
        if len(logs) != 0:
            for name, value in logs.items():
                logs[name] = np.mean(value)
            train_logs = {
                f'Train - {m}': v
                for m, v in logs.items()
            }
        else:
            logs = {
                m.split(' - ')[1]: v
                for m, v in train_logs.items()
            }

        print_logs('Train', logs)

        logs = eval_model(model, val_loader, config)
        print_logs('Eval', logs)
        val_logs = {
            f'Validation - {m}': v
            for m, v in logs.items()
        }

        val_source, val_target = val_dataset[ torch.randint(len(val_dataset), (1,)) ]
        val_pred, val_prob = beam_search(
            model,
            val_source,
            config['src_vocab'],
            config['tgt_vocab'],
            config['src_tokenizer'],
            device,  # It can take a lot of VRAM
            beam_width=10,
            max_target=100,
            max_sentence_length=config['max_sequence_length'],
        )[0]
        print(val_source)
        print(val_pred)

        logs = {**train_logs, **val_logs}  # Merge dictionnaries
        wandb.log(logs)  # Upload to the WandB cloud

        # Table logs
        train_source, train_target = train_dataset[ torch.randint(len(train_dataset), (1,)) ]
        train_pred, train_prob = beam_search(
            model,
            train_source,
            config['src_vocab'],
            config['tgt_vocab'],
            config['src_tokenizer'],
            device,  # It can take a lot of VRAM
            beam_width=10,
            max_target=100,
            max_sentence_length=config['max_sequence_length'],
        )[0]

        data = [
            e + 1,
            train_source, train_target, train_pred, train_prob,
            val_source, val_target, val_pred, val_prob,
        ]
        log_table.add_data(*data)

    # Log the table at the end of the training
    wandb.log({'Model predictions': log_table})


# # Training the models
# We can now finally train the models.
# Choose the right hyperparameters, play with them and try to find
# ones that lead to good models and good training curves.
# Try to reach a loss under 1.0.
# 
# So you know, it is possible to get descent results with approximately 20 epochs.
# With CUDA enabled, one epoch, even on a big model with a big dataset, shouldn't last more than 10 minutes.
# A normal epoch is between 1 to 5 minutes.
# 
# *This is considering Colab Pro, we should try using free Colab to get better estimations.*
# 
# ---
# 
# To test your implementations, it is easier to try your models
# in a CPU instance. Indeed, Colab reduces your GPU instances priority
# with the time you recently past using GPU instances. It would be
# sad to consume all your GPU time on implementation testing.
# Moreover, you should try your models on small datasets and with a small number of parameters.
# For exemple, you could set:
# ```
# MAX_SEQ_LEN = 10
# MIN_TOK_FREQ = 20
# dim_embedding = 40
# dim_hidden = 60
# n_layers = 1
# ```
# 
# You usually don't want to log anything onto WandB when testing your implementation.
# To deactivate WandB without having to change any line of code, you can type `!wandb offline` in a cell.
# 
# Once you have rightly implemented the models, you can train bigger models on bigger datasets.
# When you do this, do not forget to change the runtime as GPU (and use `!wandb online`)!

# In[ ]:


# Checking GPU and logging to wandb
import locale
locale.getpreferredencoding = lambda: "UTF-8"

get_ipython().system('wandb login')

get_ipython().system('nvidia-smi')


# In[ ]:


# Instanciate the datasets

MAX_SEQ_LEN = 60
MIN_TOK_FREQ = 2
train_dataset, val_dataset = build_datasets(
    MAX_SEQ_LEN,
    MIN_TOK_FREQ,
    en_tokenizer,
    fr_tokenizer,
    train,
    valid,
)


print(f'English vocabulary size: {len(train_dataset.en_vocab):,}')
print(f'French vocabulary size: {len(train_dataset.fr_vocab):,}')

print(f'\nTraining examples: {len(train_dataset):,}')
print(f'Validation examples: {len(val_dataset):,}')


# In[ ]:


# Build the model, the dataloaders, optimizer and the loss function
# Log every hyperparameters and arguments into the config dictionnary

config = {
    # General parameters
    'epochs': 5,
    'batch_size': 128,
    'lr': 1e-3,
    'betas': (0.9, 0.99),
    'clip': 5,
    'device': 'cuda' if torch.cuda.is_available() else 'cpu',

    # Model parameters
    'n_tokens_src': len(train_dataset.en_vocab),
    'n_tokens_tgt': len(train_dataset.fr_vocab),
    'n_heads': 4,
    'dim_embedding': 196,
    'dim_hidden': 256,
    'n_layers': 3,
    'dropout': 0.1,
    'model_type': 'RNN',

    # Others
    'max_sequence_length': MAX_SEQ_LEN,
    'min_token_freq': MIN_TOK_FREQ,
    'src_vocab': train_dataset.en_vocab,
    'tgt_vocab': train_dataset.fr_vocab,
    'src_tokenizer': en_tokenizer,
    'tgt_tokenizer': fr_tokenizer,
    'src_pad_idx': train_dataset.en_vocab['<pad>'],
    'tgt_pad_idx': train_dataset.fr_vocab['<pad>'],
    'seed': 0,
    'log_every': 50,  # Number of batches between each wandb logs
}

torch.manual_seed(config['seed'])

config['train_loader'] = DataLoader(
    train_dataset,
    batch_size=config['batch_size'],
    shuffle=True,
    collate_fn=lambda batch: generate_batch(batch, config['src_pad_idx'], config['tgt_pad_idx'])
)

config['val_loader'] = DataLoader(
    val_dataset,
    batch_size=config['batch_size'],
    shuffle=True,
    collate_fn=lambda batch: generate_batch(batch, config['src_pad_idx'], config['tgt_pad_idx'])
)
"""
model = TranslationRNN(
    config['n_tokens_src'],
    config['n_tokens_tgt'],
    config['dim_embedding'],
    config['dim_hidden'],
    config['n_layers'],
    config['dropout'],
    config['src_pad_idx'],
    config['tgt_pad_idx'],
    config['model_type'],
)
"""
model = TranslationTransformer(
    config['n_tokens_src'],
    config['n_tokens_tgt'],
    config['n_heads'],
    config['dim_embedding'],
    config['dim_hidden'],
    config['n_layers'],
    config['dropout'],
    config['src_pad_idx'],
    config['tgt_pad_idx'],
)

config['optimizer'] = optim.Adam(
    model.parameters(),
    lr=config['lr'],
    betas=config['betas'],
)

weight_classes = torch.ones(config['n_tokens_tgt'], dtype=torch.float)
weight_classes[config['tgt_vocab']['<unk>']] = 0.1  # Lower the importance of that class
config['loss'] = nn.CrossEntropyLoss(
    weight=weight_classes,
    ignore_index=config['tgt_pad_idx'],  # We do not have to learn those
)

summary(
    model,
    input_size=[
        (config['batch_size'], config['max_sequence_length']),
        (config['batch_size'], config['max_sequence_length'])
    ],
    dtypes=[torch.long, torch.long],
    depth=3,
)


# In[ ]:


get_ipython().system('wandb online  # online / offline to activate or deactivate WandB logging')

with wandb.init(
        config=config,
        project='INF8225 - TP3',  # Title of your project
        group='Transformer - small',  # In what group of runs do you want this run to be in?
        save_code=True,
    ):
   train_model(model, config)


# In[ ]:


sentence = "It is possible to try your work here."

preds = beam_search(
    model,
    sentence,
    config['src_vocab'],
    config['tgt_vocab'],
    config['src_tokenizer'],
    config['device'],
    beam_width=10,
    max_target=100,
    max_sentence_length=config['max_sequence_length']
)[:5]

for i, (translation, likelihood) in enumerate(preds):
    print(f'{i}. ({likelihood*100:.5f}%) \t {translation}')


# # Questions
# 1. Explain the differences between Vanilla RNN, GRU-RNN, and Transformers.
# 2. Why is positionnal encoding necessary in Transformers and not in RNNs?
# 3. Describe the preprocessing process. Detail how the initial dataset is processed before being fed to the translation models.

# 1. Les vanilla RNN sont des réseaux récurrents simples adaptés aux tâches de modélisation de phrases de base. Ils travaillent en traitant les données d’entrée une étape à la fois, chaque étape mettant à jour un état caché qui résume l’information vue jusqu’à présent. Cependant, le problème avec les ARN simples est qu’ils peuvent souffrir de disparition ou d’explosion des gradients, ce qui peut conduire à des problèmes avec l’apprentissage des dépendances à long terme dans les données.
# Les GRU-RNN sont une variante plus avancée capable de gérer des phrases plus longues et des motifs complexes. Les GRUs utilisent des
# gates pour mettre à jour sélectivement l’état caché à chaque étape de temps, ce qui aide à conserver les informations importantes tout en filtrant les données non pertinentes. En comparaison avec les RNNs vanille, les GRUs ont moins de paramètres et sont plus rapides à former.
# Les transformateurs (transformers) sont un type d’architecture de réseau neuronal. Contrairement aux RNNs, les transformateurs ne comptent pas sur les connexions récurrentes pour traiter les données séquentielles. Ils utilisent des mécanismes d’attention pour peser dynamiquement l’importance des différentes parties de la séquence d’entrée, leur permettant d’apprendre plus efficacement les dépendances à long terme et à traiter les séquences en parallèle.
# 
# 2. Dans les RNN, la phrase d'entrée est traitée séquentiellement, un mot à la fois, ce qui leur permet de capturer implicitement la position de chaque élément de la phrase, car l'état caché est mis à jour à chaque étape temporelle. Du coup, les informations positionnelles sont naturellement encodées dans les états cachés des RNN, et aucun positionnal encoding n'est nécessaire.
# Dans les Transformers, les phrases d’entrées sont traitées en parallèle plutôt que séquentiellement. A cause de cela, les Transformers ne capturent pas la position de chaque élément de la séquence, d’où le fait qu’on rajoute un positionnal encoding pour fournir ces informations positionnelles manquantes qui sont obligatoires pour bien traduire des phrases.
# 
# 3. Le processus de prétraitement des tâches de traduction automatique implique plusieurs étapes pour préparer l'ensemble de données source avant de l'envoyer au modèle de traduction. Ces étapes sont essentielles pour garantir la qualité des données d'entrée et leur compatibilité avec les exigences du modèle. Voici un aperçu du processus de prétraitement :
# 
#  1. Collecte et nettoyage des données :
# Recueillir un corpus bilingue contenant des textes en langues source et cible.
# Supprimer les données non pertinentes ou bruyantes telles que les balises HTML, les caractères spéciaux et le contenu non textuel.
# Convertir le texte en minuscules, supprimez les espaces supplémentaires, corrigez les problèmes d'encodage et normalisez le texte
# 2. Tokénisation (Tokenization) :
# Diviser le texte en unités plus petites appelées jetons. Les jetons peuvent être des mots, des parties de mots ou des caractères, selon la méthode de tokenisation que vous choisissez.
# Les méthodes de tokenisation courantes incluent la tokenisation basée sur les mots, le codage par paire d'octets (BPE), SentencePiece, WordPiece.
# 3. Création de vocabulaire :
# Sélectionner les jetons les plus courants dans le texte symbolisé pour créer vos vocabulaires de langue source et cible.
# Limiter la taille du vocabulaire à un nombre gérable (comme 30 000 ou 50 000 jetons) pour réduire la complexité de calcul et éviter le surentraînement.
# 4. Encodage numérique :
# Attribuer un identifiant numérique unique à chaque jeton de vocabulaire.
# 
#  Convertir le texte tokenisé en un ensemble d'identificateurs numériques pouvant être traités par le modèle.
# 5. Rembourrage et découpage :
# Ajouter un remplissage (généralement 0) aux séquences courtes ou tronquez les séquences longues afin que toutes les séquences d'entrée aient la même longueur.
# Choisir la longueur maximale du tableau en fonction de la longueur moyenne ou médiane des tableaux de votre ensemble de données, en tenant compte des ressources de calcul disponibles et des limites du modèle.
# 6. Fractionnez l'ensemble de données :
# Diviser l'ensemble de données en un ensemble d'apprentissage, un ensemble de validation et un ensemble de test.
# L'ensemble d'apprentissage est utilisé pour former le modèle, l'ensemble de validation est utilisé pour régler le modèle et surveiller ses performances pendant l'apprentissage, et l'ensemble de test est utilisé pour évaluer les performances du modèle par rapport à des données non publiées.
# 7. Traitement par lots :
# Regroupez les séquences en lots pour utiliser efficacement les ressources de calcul pendant la formation.
# Mélangez l'ensemble d'apprentissage afin que le modèle soit exposé à différents ensembles d'exemples à chaque époque.
# Une fois le processus de prétraitement terminé, l'ensemble de données résultant, qui se compose de séquences numériques
# 
# représentant des phrases dans les langues source et cible, peut être envoyé à un modèle de traduction pour la formation et la notation.

# # Small report - experiments
# Once everything is working fine, you can explore aspects of these models and do some research of your own into how they behave.
# 
# For exemple, you can experiment with the hyperparameters.
# What are the effect of the differents hyperparameters with the final model performance? What about training time?
# 
# What are some other metrics you could have for machine translation? Can you compute them and add them to your WandB report?
# 
# Those are only examples, you can do whatever you think will be interesting.
# This part account for many points, *feel free to go wild!*
# 
# ---
# *Make a concise report about your experiments here.*

# ### Exploring different hyperparameters
# Nous avons explorer différents paramètres pour les trois modèles. En premier lieu, on a fait varier les paramètres généraux tels que epoch, la taille du lot (batch size), le taux d’apprentissage (learning rate). Puis dans un deuxième temps, on varie les paramètres de chaque modèle comme le nombres de couches (layers number) et le taux d’abandon (dropout). On affichera les métriques principalement pour le modèle transformer.
# Le taux d’apprentissage (learning rate)
# L’un des hyperparamètres de l’optimisateur est le taux d’apprentissage. Nous avons essayé avec les valeurs : 1e-5, 5e-3, 1e-3. Le taux d’apprentissage contrôle la taille des pas pour qu’un modèle atteigne la fonction de perte minimale. Un taux d’apprentissage plus élevé permet au modèle d’apprendre plus rapidement, mais il peut manquer la fonction de perte minimale et n’atteindre que son environnement. Un taux d’apprentissage plus faible donne une meilleure chance de trouver une fonction de perte minimale (loss function). Cependant, ce taux d’apprentissage plus faible nécessitera des époques (epochs) plus élevées, ou plus de ressources en temps et en mémoire .
# On a testé les trois valeurs avec le modèle RNN. On a le groupe RNN batch correspond à un learning rate de 1e-3. On remarque alors que plus le learning rate est grand, plus les métriques train-loss ainsi que validation loss sont performantes.
# 
# ### La taille du lot (Batch size) :
# La taille du lot est également un hyper paramètre à considérer. Nous avons essayé avec les valeurs : 64 , 128, 256. Plus la taille d’observation de l’ensemble de données de formation est grande, plus ça nécessite de temps pour construire le modèle. On aurait aimé entrainer le modèle avec des tailles plus importantes mais le temps pour entraîner chaque modèle s’approche déjà de 30-40 minutes avec 256. Pour accélérer l’apprentissage du modèle, on peut attribuer une taille de lot de sorte que toutes les données de formation ne sont pas données au modèle en même temps. La taille du lot est le nombre de sous-échantillons de données de formation pour l’entrée.
# On a testé les différentes valeurs du batch size sur le transformer. Cependant on a aussi changé le nombre de couches pour la batch size 256, on a mis 5 couches au lieu de 3. Donc la comparaison n'est pas exacte. Mais on peut conclure à travers les différentes métriques que plus la taille du lot est petite, plus le processus d’apprentissage accélère mais la variance de l’exactitude des ensembles de données de validation est plus élevée. Une plus grande taille de lot a un processus d’apprentissage plus lent, mais l’exactitude de l’ensemble de données de validation a une variance plus faible.
# 
# ### Epoch
# Le nombre de fois qu’un ensemble de données entier est passé par le modèle de réseau neural est appelé une époque. On a essayé avec les valeurs : 5, 10, 20. Une époque signifie que l’ensemble de données d’entraînement est passé en avant et en arrière par le réseau neuronal une fois. Un trop petit nombre d’époques se traduit par un sous-ajustement (underfitting) parce que le réseau neuronal n’a pas suffisamment appris. L’ensemble de données de formation doit passer plusieurs fois ou plusieurs époques sont requises. D’autre part, trop d’époques mèneront au débordement où le modèle peut prédire les données très bien, mais ne peut pas prédire de nouvelles données invisibles assez bien. Le nombre d’époques doit être réglé pour obtenir le résultat optimal. Cependant, plus le nombre d’époques est grand, plus le temps d’entrainement du modèle est élevée. Lorsqu’on faisait 20 époques, cela prenait plus de 50 minutes. En plus du temps, notre modèle ne prédisait pas assez bien pour avoir besoin d’augmenter ce paramètres. On a commencé à tester 20 epochs pour transformer mais vu le temps d'entrainement. On a décidé de tester que 5 et 10 epochs pour RNN et GRU.
# 
# 
# ### Le nombre de couches (number of layers)
# Les couches pour les différents modèles est également un hyperparamètre à considérer lors de la prédiction du modèle. Un problème plus complexe nécessite plus de couches pour construire le modèle. On a essayé avec les valeurs : 3, 5, 10 pour le modèle Transformer. On remarque à travers les différents métriques que plus le nombre de couches est élevée, mieux le modèle prédit les données.
# 
# 
# ### Le taux d’abandon (dropout rate)
# Une couche de régularisation est la couche Dropout. La couche Dropout, comme son nom l’indique, dépose au hasard un certain nombre de neurones dans une couche. Les neurones lâchés ne sont plus utilisés. Le taux de pourcentage de neurones à descendre est fixé dans le taux d’abandon. On a essayé avec les valeurs : 0.1 , 0.3, 0.5 pour le modèle de Transformer. Pour le dropout rate 0.3 on a aussi modifié le nombre de couches de 3 à 5. Donc la comparaison des métriques n'est pas exacte mais elle permet de conclure qu'un plus grand taux d'abandon permet d'améliorer la performance de notre modèle.
# 
# ### Comprendre la recherche avide (greedy search) et la recherche par faisceau (beam search)
# La recherche avide (greedy search) et la recherche par faisceau (beam search) sont des algorithmes bien connus dans les tâches de génération de langage de NLP (Traitement du langage naturel). La recherche avide et la recherche de faisceau visent à générer les sorties de séquence de jetons à partir d’un modèle de réseau neuronal. Les deux approches sont axées sur des modèles séquentiels.
# La recherche avide (greedy search) consiste à prendre le jeton avec la probabilité conditionnelle la plus élevée du vocabulaire V.
# La recherche de faisceau (beam search) est une meilleure version. La recherche de faisceau a un paramètre appelé beam_width. Le beam_width est le nombre de jetons avec les probabilités conditionnelles les plus élevées à chaque étape de temps t. Dans notre modèle, le beam_width est initialement égale à 10. On a essayé avec différentes valeurs tels que 5, 20,40. On obtient la même performance ce qui apparait plutôt incorrect.
# Hyperparameter beam_width from beam search
# Les Inconvénients de cet hyperparamètre, en augmentant la taille du beam_width, la qualité de la séquence de sortie s’améliore considérablement, mais elle réduit la vitesse du décodeur. Il y a un point de saturation, où même si on augmente le nombre de beam_width, la qualité du décodage ne s’améliore plus.
# 
# ### Finding the best parameters
# On a pu obtenir une loss proche de 1 pour le modèle Transformer avec les paramètres :
# batch_size 128, n_layers : 5, dropout : 0.3, epoch : 5, lr (learning rate) : 1e-9
# qui correspond au meilleur modèle qu'on a entrainé.
# 
