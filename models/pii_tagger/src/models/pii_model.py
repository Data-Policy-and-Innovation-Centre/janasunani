"""PII Transformer-CRF model definition.
 
Inference-only. Training pipeline has been removed as the pipeline
operates on pre-trained weights against English documents only.
"""
 
import logging
 
import torch
import torch.nn as nn
from torch import Tensor
from torchcrf import CRF
from transformers import AutoModel
 
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
logger = logging.getLogger(__name__)
 
 
class TransformerCRF(nn.Module):
    """Transformer encoder + linear classifier + CRF for token classification.
 
    Parameters
    ----------
    model_name : str
        Hugging Face model name for the encoder.
    num_labels : int
        Number of output label classes.
    dropout : float
        Dropout probability applied before classification.
    class_weights : torch.Tensor or None
        Optional weight tensor applied to emissions.
    """
 
    def __init__(
        self: "TransformerCRF",
        model_name: str,
        num_labels: int,
        dropout: float,
        class_weights: torch.Tensor | None,
    ) -> None:
        super().__init__()
        self.encoder = AutoModel.from_pretrained(
            model_name,
            use_safetensors=True,
            trust_remote_code=True,
        )
        self.dropout = nn.Dropout(dropout)
        self.classifier = nn.Linear(self.encoder.config.hidden_size, num_labels)
        self.crf = CRF(num_labels, batch_first=True)
        self.class_weights = class_weights
 
    def forward(
        self: "TransformerCRF",
        ids: Tensor,
        mask: Tensor,
        labels: Tensor | None = None,
    ) -> Tensor | list[list[int]]:
        """Forward pass of the Transformer-CRF model.
 
        Parameters
        ----------
        ids : torch.Tensor
            Input token IDs of shape (batch, seq_len).
        mask : torch.Tensor
            Attention mask of shape (batch, seq_len).
        labels : torch.Tensor or None, optional
            If provided, returns loss. If None, returns decoded sequences.
 
        Returns:
        -------
        torch.Tensor or list[list[int]]
            Loss tensor if labels provided, else decoded label sequences.
        """
        out = self.encoder(input_ids=ids, attention_mask=mask)
        emissions = self.classifier(self.dropout(out.last_hidden_state))
 
        if self.class_weights is not None:
            emissions = emissions * self.class_weights
 
        if labels is not None:
            loss = -self.crf(emissions, labels, mask=mask.bool(), reduction="mean")
            return loss
 
        return self.crf.decode(emissions, mask=mask.bool())
 
 
def build_transformer_crf(
    model_name: str,
    num_labels: int,
    dropout: float = 0.1,
    class_weights: torch.Tensor | None = None,
) -> nn.Module:
    """Create and initialise a Transformer-CRF model on the active device.
 
    Parameters
    ----------
    model_name : str
        Hugging Face model name for the encoder.
    num_labels : int
        Number of label classes.
    dropout : float, optional
        Dropout probability before the classifier.
    class_weights : torch.Tensor or None, optional
        Optional emission-weighting tensor.
 
    Returns:
    -------
    TransformerCRF
        Initialised model placed on the selected device.
    """
    return TransformerCRF(model_name, num_labels, dropout, class_weights).to(device)
 