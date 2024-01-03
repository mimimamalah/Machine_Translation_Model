# Machine Translation Model

## Description
This project implements a machine translation model in Python, focusing on translating English sentences into French. It explores three neural network architectures: Vanilla RNN, GRU-RNN, and Transformer, providing a comparative analysis of their performance in machine translation tasks.

## Features
- English to French translation.
- Implementations of Vanilla RNN, GRU-RNN, and Transformer architectures.
- Evaluation and comparison of different model performances.
- GPU utilization for efficient computation.

## Installation
Before running the `MachineTranslationModel.py` script, ensure the following dependencies are installed:

1. **Python Packages**:
   Install the required Python packages using:
   ```bash
   pip install torch torchtext spacy torchinfo einops wandb
   ```

2. **Spacy Language Models**:
   Download the necessary Spacy language models for English and French:
   ```bash
   python3 -m spacy download en
   python3 -m spacy download fr
   ```

## Usage
To use this translation model, run the `MachineTranslationModel.py` script. Ensure you have a GPU runtime for optimal performance.

## Dataset
The "Tab-delimited Bilingual Sentence Pairs" dataset from [ManyThings.org](http://www.manythings.org/anki/) is used, offering a collection of English-French sentence pairs for training and evaluation.

## Implementation Details
- **Data Preprocessing**: Includes downloading, parsing, and preparing the dataset, building vocabularies, and creating torch datasets.
- **Model Architectures**: 
  - **Vanilla RNN**: Basic RNN for translation.
  - **GRU-RNN**: Advanced RNN using Gated Recurrent Units.
  - **Transformer**: Based on the attention mechanism for non-sequential data processing.
- **Training and Evaluation**: Models are trained and evaluated, with comparative performance metrics.

## Acknowledgements
- Inspired by the [PyTorch translation tutorial](https://pytorch.org/tutorials/beginner/torchtext_translation_tutorial.html).
- Dataset sourced from [ManyThings.org](http://www.manythings.org/anki/).
