import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
from time import time


def f_m(v1, v2, W):
    """
    
    :param v1: [s, B, D]
    :param v2: [s, B, D]
    :param W: [l, D]
    :return: [s, B, l]
    """
    seq_len, batch_size, dim = v1.size()
    l = W.size()[0]
    W_rep = W.repeat(seq_len, batch_size, 1, 1)
    v1_rep = v1.repeat(l, 1, 1, 1).permute(1, 2, 0, 3)
    v2_rep = v2.repeat(l, 1, 1, 1).permute(1, 2, 0, 3)

    result = F.cosine_similarity(W_rep * v1_rep, W_rep * v2_rep, dim=3).view(seq_len, batch_size, l)

    return result


def f_m_multi(v1, v2, W):
    """
    f_m for multi and multi
    :param v1: [s,B,D]
    :param v2: [s,B,D]
    :param W: [l,D]
    :return: [s,s,B,l]
    """
    seq_len, batch_size, dim = v1.size()
    l = W.size()[0]
    W_rep = W.repeat(seq_len, seq_len, batch_size, 1, 1)
    v1_rep = v1.repeat(l, 1, 1, 1).permute(1, 2, 0, 3).unsqueeze(1)
    v2_rep = v2.repeat(l, 1, 1, 1).permute(1, 2, 0, 3).unsqueeze(0)

    result = F.cosine_similarity(W_rep * v1_rep, W_rep * v2_rep, dim=4).view(seq_len, seq_len, batch_size, l)

    return result


def max_pool_matching(p, q, W):
    """
    
    :param p_fws: [seq_len, batch_size, D]
    :param p_bws: [seq_len, batch_size, D]
    :param q_fws: [seq_len, batch_size, D]
    :param q_bws: [seq_len, batch_size, D]
    :param W3: [l, D]
    :param W4: [l, D]
    :return: [batch_size]
    """
    seq_len, batch_size, dim = p.size()
    l = W.size()[0]

    m_max = f_m_multi(p, q, W).squeeze()

    m_max = m_max.max(1)[0]

    assert m_max.size() == torch.Size([seq_len, batch_size, l])

    return m_max


def attentive_matching(p, q, W0, W1):
    """
    
    :param p: [s, B, D]
    :param q: [s, B, D]
    :param Ws: [4, l, D]
    :return: 
    """
    seq_len, batch_size, dim = p.size()

    # [seq_len_i, seq_len_j, dim]
    alpha = F.cosine_similarity(p.unsqueeze(1), q.unsqueeze(0), 3)
    assert alpha.size() == torch.Size([seq_len, seq_len, batch_size])

    h_mean = (alpha.unsqueeze(3) * q).sum(1) / (alpha.sum(1).unsqueeze(2))

    m_att = f_m(p, h_mean, W0)

    _, h_maxatt_idx = alpha.max(1)
    assert h_maxatt_idx.size() == torch.Size([seq_len, batch_size])

    # [s_i, B, D]
    h_maxatt = q.gather(0, h_maxatt_idx.repeat(dim, 1, 1).permute(1, 2, 0))
    assert h_maxatt.size() == torch.Size([seq_len, batch_size, dim])

    m_maxatt = f_m(p, h_maxatt, W1)

    return m_att, m_maxatt


class MatchingLayer(nn.Module):
    def __init__(self, hidden_dim=100, perspectives=4,
                 full_match=True, maxpool_match=True, att_match=True, maxatt_match=True):
        super(MatchingLayer, self).__init__()
        self.W = nn.ParameterList([nn.Parameter(torch.randn(perspectives, hidden_dim)) for i in range(8)])

        self.full_match = full_match
        self.maxpool_match = maxpool_match
        self.att_match = att_match
        self.maxatt_match = maxatt_match

        self.perspectives = perspectives

    def forward(self, p_contexts, q_contexts):
        # p to q
        matching_vecs = self.matching(p_contexts, q_contexts, self.W, l=self.perspectives)

        return matching_vecs
        # return p_contexts, q_contexts

    def matching(self, p_context, q_context, W, l=4):
        """

        :param p_context: [1, batch, D]
        :param q_context: [seq_len, batch, D]
        :param W: [8, l, D]
        :param l: number of perspectives
        :return: [B, l]
        """
        seq_len, batch, hidden_size_2 = p_context.size()
        half = int(hidden_size_2 / 2)
        p_context_fw, p_context_bw = p_context.split(half, -1)
        q_context_fw, q_context_bw = q_context.split(half, -1)

        matching_vecs = []

        if self.full_match:
            m_full_fws = f_m(p_context_fw, q_context_fw[-1].repeat(seq_len, 1, 1), W[0])
            m_full_bws = f_m(p_context_bw, q_context_bw[0].repeat(seq_len, 1, 1), W[1])
            matching_vecs.append(m_full_fws)
            matching_vecs.append(m_full_bws)

        if self.maxpool_match:
            m_max_pool_fws = max_pool_matching(p_context_fw, q_context_fw, W[2])
            m_max_pool_bws = max_pool_matching(p_context_bw, q_context_bw, W[3])
            matching_vecs.append(m_max_pool_fws)
            matching_vecs.append(m_max_pool_bws)


        m_att_fws, m_maxatt_fws = attentive_matching(p_context_fw, q_context_fw, W[4], W[5])
        m_att_bws, m_maxatt_bws = attentive_matching(p_context_bw, q_context_bw, W[4], W[5])

        if self.att_match:
            matching_vecs.append(m_att_fws)
            matching_vecs.append(m_att_bws)

        if self.maxatt_match:
            matching_vecs.append(m_maxatt_fws)
            matching_vecs.append(m_maxatt_bws)

        assert m_full_fws.size() == m_full_bws.size()
        assert m_full_fws.size() == torch.Size([seq_len, batch, l])

        # concat forward and backward
        matching_vecs = torch.cat(matching_vecs, dim=-1)

        return matching_vecs

if __name__ == "__main__":
    test_input_p = Variable(torch.randn(5, 128, 200))
    test_input_q = Variable(torch.randn(5, 128, 200))
    l = 5

    ml = MatchingLayer(perspectives=l)

    p_vecs, q_vecs = ml(test_input_p, test_input_q)

    assert p_vecs.size() == q_vecs.size()
    assert p_vecs.size() == torch.Size([5, 128, 8*l])
