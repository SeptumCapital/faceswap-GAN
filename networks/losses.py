from keras.layers import Lambda, concatenate
from tensorflow.contrib.distributions import Beta
from .instance_normalization import InstanceNormalization
import keras.backend as K
import tensorflow as tf

def first_order(x, axis=1):
    img_nrows = x.shape[1]
    img_ncols = x.shape[2]
    if axis == 1:
        return K.abs(x[:, :img_nrows - 1, :img_ncols - 1, :] - x[:, 1:, :img_ncols - 1, :])
    elif axis == 2:
        return K.abs(x[:, :img_nrows - 1, :img_ncols - 1, :] - x[:, :img_nrows - 1, 1:, :])
    else:
        return None   

def calc_loss(pred, target, loss='l2'):
    if loss.lower() == "l2":
        return K.mean(K.square(pred - target))
    elif loss.lower() == "l1":
        return K.mean(K.abs(pred - target))
    elif loss.lower() == "cross_entropy":
        return -K.mean(K.log(pred + K.epsilon())*target + K.log(1 - pred + K.epsilon())*(1 - target))
    else:
        raise ValueError(f'Recieve an unknown loss type: {loss}.')
    
def cyclic_loss(netG1, netG2, real1):
    fake2 = netG2(real1) # fake2 ABGR
    fake2 = Lambda(lambda x: x[:,:,:, 1:])(fake2) # fake2 BGR
    cyclic1 = netG1(fake2) # cyclic1 ABGR
    cyclic1 = Lambda(lambda x: x[:,:,:, 1:])(cyclic1) # cyclic1 BGR
    loss = calc_loss(cyclic1, real1, loss='l1')
    return loss

def adversarial_loss(netD, real, fake_abgr, distorted, **weights):   
    alpha = Lambda(lambda x: x[:,:,:, :1])(fake_abgr)
    fake_bgr = Lambda(lambda x: x[:,:,:, 1:])(fake_abgr)
    fake = alpha * fake_bgr + (1-alpha) * distorted
    
    dist = Beta(0.2, 0.2)
    lam = dist.sample()
    mixup = lam * concatenate([real, distorted]) + (1 - lam) * concatenate([fake, distorted])        
    output_mixup = netD(mixup)
    loss_D = calc_loss(output_mixup, lam * K.ones_like(output_mixup), "l2")
    loss_G = weights['w_D'] * calc_loss(output_mixup, (1 - lam) * K.ones_like(output_mixup), "l2")
    mixup2 = lam * concatenate([real, distorted]) + (1 - lam) * concatenate([fake_bgr, distorted])
    output_mixup2 = netD(mixup2)
    loss_D += calc_loss(output_mixup2, lam * K.ones_like(output_mixup2), "l2")
    loss_G += weights['w_D'] * calc_loss(output_mixup2, (1 - lam) * K.ones_like(output_mixup2), "l2")
    return loss_D, loss_G

def reconstruction_loss(real, fake_abgr, mask_eyes, **weights):
    alpha = Lambda(lambda x: x[:,:,:, :1])(fake_abgr)
    fake_bgr = Lambda(lambda x: x[:,:,:, 1:])(fake_abgr)
    
    loss_G = 0
    loss_G += weights['w_recon'] * calc_loss(fake_bgr, real, "l1")
    loss_G += weights['w_eyes'] * K.mean(K.abs(mask_eyes*(fake_bgr - real)))    
    return loss_G

def edge_loss(real, fake_abgr, mask_eyes, **weights):
    alpha = Lambda(lambda x: x[:,:,:, :1])(fake_abgr)
    fake_bgr = Lambda(lambda x: x[:,:,:, 1:])(fake_abgr)
    
    loss_G = 0
    loss_G += weights['w_edge'] * calc_loss(first_order(fake_bgr, axis=1), first_order(real, axis=1), "l1")  
    loss_G += weights['w_edge'] * calc_loss(first_order(fake_bgr, axis=2), first_order(real, axis=2), "l1") 
    shape_mask_eyes = mask_eyes.get_shape().as_list()
    resized_mask_eyes = tf.image.resize_images(mask_eyes, [shape_mask_eyes[1]-1, shape_mask_eyes[2]-1]) 
    loss_G += weights['w_eyes'] * K.mean(K.abs(resized_mask_eyes * \
                                               (first_order(fake_bgr, axis=1) - first_order(real, axis=1))))
    loss_G += weights['w_eyes'] * K.mean(K.abs(resized_mask_eyes * \
                                               (first_order(fake_bgr, axis=2) - first_order(real, axis=2)))) 
    return loss_G
    
def perceptual_loss(real, fake_abgr, distorted, mask_eyes, vggface_feats, **weights): 
    alpha = Lambda(lambda x: x[:,:,:, :1])(fake_abgr)
    fake_bgr = Lambda(lambda x: x[:,:,:, 1:])(fake_abgr)
    fake = alpha * fake_bgr + (1-alpha) * distorted
    
    def preprocess_vggface(x):
        x = (x + 1)/2 * 255 # channel order: BGR
        x -= [91.4953, 103.8827, 131.0912]
        return x    
    
    real_sz224 = tf.image.resize_images(real, [224, 224])
    real_sz224 = Lambda(preprocess_vggface)(real_sz224)
    dist = Beta(0.2, 0.2)
    lam = dist.sample() # use mixup trick here to reduce foward pass from 2 times to 1.
    mixup = lam*fake_bgr + (1-lam)*fake
    fake_sz224 = tf.image.resize_images(mixup, [224, 224])
    fake_sz224 = Lambda(preprocess_vggface)(fake_sz224)
    real_feat112, real_feat55, real_feat28, real_feat7 = vggface_feats(real_sz224)
    fake_feat112, fake_feat55, fake_feat28, fake_feat7  = vggface_feats(fake_sz224)
    
    # Apply instance norm on VGG(ResNet) features
    # From MUNIT https://github.com/NVlabs/MUNIT
    loss_G = 0
    def instnorm(): return InstanceNormalization()
    loss_G += weights['w_pl'][0] * calc_loss(instnorm()(fake_feat7), instnorm()(real_feat7), "l2") 
    loss_G += weights['w_pl'][1] * calc_loss(instnorm()(fake_feat28), instnorm()(real_feat28), "l2")
    loss_G += weights['w_pl'][2] * calc_loss(instnorm()(fake_feat55), instnorm()(real_feat55), "l2")
    loss_G += weights['w_pl'][3] * calc_loss(instnorm()(fake_feat112), instnorm()(real_feat112), "l2")
    return loss_G