"""
python implementation of the semi-global matching algorithm from Stereo Processing by Semi-Global Matching
and Mutual Information (https://core.ac.uk/download/pdf/11134866.pdf) by Heiko Hirschmuller.

author: David-Alexandre Beaupre
date: 2019/07/12
"""

import argparse
import sys
import time as t

import cv2
import numpy as np


class Direction:
    def __init__(self, direction=(0, 0), name='invalid'):
        """
        represent a cardinal direction in image coordinates (top left = (0, 0) and bottom right = (1, 1)).
        :param direction: (x, y) for cardinal direction.
        :param name: common name of said direction.
        """
        self.direction = direction
        self.name = name


# 8 defined directions for sgm
N = Direction(direction=(0, -1), name='north')
NE = Direction(direction=(1, -1), name='north-east')
E = Direction(direction=(1, 0), name='east')
SE = Direction(direction=(1, 1), name='south-east')
S = Direction(direction=(0, 1), name='south')
SW = Direction(direction=(-1, 1), name='south-west')
W = Direction(direction=(-1, 0), name='west')
NW = Direction(direction=(-1, -1), name='north-west')


class Paths:
    def __init__(self):
        """
        represent the relation between the directions.
        """
        self.paths = [N, NE, E, SE, S, SW, W, NW]
        self.size = len(self.paths)
        self.effective_paths = [(E,  W), (SE, NW), (S, N), (SW, NE)]


class Parameters:
    def __init__(self, max_disparity=64, P1=5, P2=70, csize=(7, 7), bsize=(3, 3)):
        """
        represent all parameters used in the sgm algorithm.
        :param max_disparity: maximum distance between the same pixel in both images.
        :param P1: penalty for disparity difference = 1
        :param P2: penalty for disparity difference > 1
        :param csize: size of the kernel for the census transform.
        :param bsize: size of the kernel for blurring the images and median filtering.
        """
        self.max_disparity = max_disparity
        self.P1 = P1
        self.P2 = P2
        self.csize = csize
        self.bsize = bsize


def load_images(left_name, right_name, parameters):
    """
    read and blur stereo image pair.
    :param left_name: name of the left image.
    :param right_name: name of the right image.
    :param parameters: structure containing parameters of the algorithm.
    :return: blurred left and right images.
    """
    left = cv2.imread(left_name, 0)
    left = cv2.GaussianBlur(left, parameters.bsize, 0, 0)
    right = cv2.imread(right_name, 0)
    right = cv2.GaussianBlur(right, parameters.bsize, 0, 0)
    return left, right


def get_indices(offset, dim, direction, height):
    """
    for the diagonal directions (SE, SW, NW, NE), return the array of indices for the current slice.
    :param offset: difference with the main diagonal of the cost volume.
    :param dim: number of elements along the path.
    :param direction: current aggregation direction.
    :param height: H of the cost volume.
    :return: arrays for the y (H dimension) and x (W dimension) indices.
    """
    y_indices = []
    x_indices = []

    for i in range(0, dim):
        if direction == SE.direction:
            if offset < 0:
                y_indices.append(-offset + i)
                x_indices.append(0 + i)
            else:
                y_indices.append(0 + i)
                x_indices.append(offset + i)

        if direction == SW.direction:
            if offset < 0:
                y_indices.append(height + offset - i)
                x_indices.append(0 + i)
            else:
                y_indices.append(height - i)
                x_indices.append(offset + i)

    return np.array(y_indices), np.array(x_indices)

def sliding_window_get_min(c_list,prev_lr,p1,p2):
    # c_list: current cost at a certain point p, having all d, dim = (1 * maxDisparity)
    # prev_lr: array of lr for point p-r, having all d, dim = ( 1 * maxDisparity)
    # p1: penalty for close neighbours
    # p2: penalty for others
    window_length = 3
    global_min = prev_lr.min()
    penalty = np.array([p1,0,p1,p2])
    cur_lr = np.zeros((1 * len(prev_lr)),dtype='int32')
    # fix_boundaries
    window = prev_lr[0:2]
    window = np.insert(window,0,window[0])
    window = np.insert(window,len(window),global_min)
    out = (window + penalty).min()
    cur_lr[0] = c_list[0] + out - global_min

    # iterate over d
    for d_index in range(1,len(prev_lr)-1):
        # get Lr(p-r, d-1) to Lr(p-r, d+ 1)
        window = prev_lr[d_index-1: d_index + window_length -1]
        window = np.insert(window,len(window),global_min)
        # element-wise add (window + penalty)
        out = (window + penalty).min()
        cur_lr[d_index] = c_list[d_index] + out - global_min

    # fix_boundaries
    window = prev_lr[-2:]
    window = np.insert(window,len(window),window[1])
    window = np.insert(window,len(window),global_min)
    out = (window + penalty).min()
    cur_lr[len(prev_lr) -1] = c_list[len(prev_lr) -1] + out - global_min

    return cur_lr

def array_get_min(row,penalty_matrix):
    dimension = row.shape[0]
    disparity = row.shape[1]
    # propagate along the long axis

    lr_array = np.zeros((dimension,disparity),dtype='int32')
    lr_array[0,:] = row[0,:]
    for i in range(1,dimension):
        prev_lr = lr_array[i-1,:]
        c_list = row[i,:]
        prev_min = np.tile(prev_lr,(disparity,1)).T
        prev_min = prev_min + penalty_matrix
        output = c_list + np.amin(prev_min,axis=0) - prev_lr.min()
        lr_array[i,:] = output
        #prev_min = np.tile(prev_lr,(1,disparity))
    return lr_array



def get_path_cost(slice, offset, parameters):
    """
    part of the aggregation step, finds the minimum costs in a D x M slice (where M = the number of pixels in the
    given direction)
    :param slice: M x D array from the cost volume.
    :param offset: ignore the pixels on the border.
    :param parameters: structure containing parameters of the algorithm.
    :return: M x D array of the minimum costs for a given slice in a given direction.
    """
    other_dim = slice.shape[0]
    disparity_dim = slice.shape[1]

    disparities = [d for d in range(disparity_dim)] * disparity_dim
    disparities = np.array(disparities).reshape(disparity_dim, disparity_dim)

    penalties = np.zeros(shape=(disparity_dim, disparity_dim), dtype=slice.dtype)
    penalties[np.abs(disparities - disparities.T) == 1] = parameters.P1
    penalties[np.abs(disparities - disparities.T) > 1] = parameters.P2

    minimum_cost_path = np.zeros(shape=(other_dim, disparity_dim), dtype=slice.dtype)
    minimum_cost_path[offset - 1, :] = slice[offset - 1, :]

    for i in range(offset, other_dim):
        previous_cost = minimum_cost_path[i - 1, :]
        current_cost = slice[i, :]
        costs = np.repeat(previous_cost, repeats=disparity_dim, axis=0).reshape(disparity_dim, disparity_dim)
        costs = np.amin(costs + penalties, axis=0)
        temp = np.amin(previous_cost)
        #if temp != 0:
        #    print(1)
        minimum_cost_path[i, :] = current_cost + costs - np.amin(previous_cost)
    return minimum_cost_path


def aggregate_costs(cost_volume, parameters, paths):
    """
    second step of the sgm algorithm, aggregates matching costs for N possible directions (8 in this case).
    :param cost_volume: array containing the matching costs.
    :param parameters: structure containing parameters of the algorithm.
    :param paths: structure containing all directions in which to aggregate costs.
    :return: H x W x D x N array of matching cost for all defined directions.
    """
    height = cost_volume.shape[0]
    width = cost_volume.shape[1]
    disparities = cost_volume.shape[2]
    start = -(height - 1)
    end = width - 1

    aggregation_volume = np.zeros(shape=(height, width, disparities, paths.size), dtype=cost_volume.dtype)

    from scipy import sparse
    penalty_matrix = sparse.diags([np.ones(64 - 1) * 10, np.ones(64 - 1) * 10], [-1, 1], dtype='int32').A
    penalty_matrix = np.where(penalty_matrix == 0, 120, penalty_matrix)
    np.fill_diagonal(penalty_matrix, 0)

    path_id = 0
    for path in paths.effective_paths:
        print('\tProcessing paths {} and {}...'.format(path[0].name, path[1].name), end='')
        sys.stdout.flush()
        dawn = t.time()

        main_aggregation = np.zeros(shape=(height, width, disparities), dtype=cost_volume.dtype)
        opposite_aggregation = np.copy(main_aggregation)

        main = path[0]
        if main.direction == S.direction:
            start=t.time()
            for x in range(0, width):
                south = cost_volume[0:height, x, :]
                north = np.flip(south, axis=0)
                main_aggregation[:, x, :] = get_path_cost(south, 1, parameters)
                opposite_aggregation[:, x, :] = np.flip(get_path_cost(north, 1, parameters), axis=0)
            end = t.time()
            print(end-start)
            full_aggregate_direction_S = np.zeros((height, width, 64), dtype='int32')
            full_aggregate_direction_N = np.zeros((height, width, 64), dtype='int32')

            start2 = t.time()
            for column_index in range(0, width):
                column_S = cost_volume[:, column_index, :]
                full_aggregate_direction_S[:, column_index, :] = array_get_min(column_S, penalty_matrix)
                column_N = np.flip(column_S,axis=0)
                full_aggregate_direction_N[:, column_index, :]  = np.flip(array_get_min(column_N, penalty_matrix),axis=0)
            end2 = t.time()
            print(end2 - start2)
            np.testing.assert_array_equal(main_aggregation,full_aggregate_direction_S)
            np.testing.assert_array_equal(opposite_aggregation, full_aggregate_direction_N)

        if main.direction == E.direction:
            for y in range(0, height):
                #start = t.time()
                east = cost_volume[y, 0:width, :]
                west = np.flip(east, axis=0)
                main_aggregation[y, :, :] = get_path_cost(east, 1, parameters)
                opposite_aggregation[y, :, :] = np.flip(get_path_cost(west, 1, parameters), axis=0)



        if main.direction == SE.direction:
            temp_final = np.zeros((64,height,width))
            final_result = np.zeros((64, height, width))
            a = 0
            for offset in range(start, end):
                south_east = cost_volume.diagonal(offset=offset).T
                start = t.time()
                dim = south_east.shape[0]
                y_se_idx, x_se_idx = get_indices(offset, dim, SE.direction, None)
                temp_get_path = get_path_cost(south_east, 1, parameters)
                main_aggregation[y_se_idx, x_se_idx, :] = temp_get_path
                end = t.time()
                dur1 = end - start
                north_west = np.flip(south_east, axis=0)
                y_nw_idx = np.flip(y_se_idx, axis=0)
                x_nw_idx = np.flip(x_se_idx, axis=0)
                opposite_aggregation[y_nw_idx, x_nw_idx, :] = get_path_cost(north_west, 1, parameters)

                start2 = t.time()
                part_diag = south_east.shape[0]
                x_index = np.array(range(part_diag))
                y_index = np.array(range(part_diag))
                diag = array_get_min(south_east, penalty_matrix)
                temp_final[:, y_index, x_index] = diag.T
                diagnal = np.ones(max(height,width))
                #try:
                #    np.testing.assert_array_equal(temp_get_path, diag)
                #except E as e:
                #    diag = temp_get_path
                # 向左
                if offset < 0:
                    permutation_matrix = np.zeros((height, height))
                    np.fill_diagonal(permutation_matrix[-offset:], diagnal)
                    permutation_3d = np.zeros((disparities, permutation_matrix.shape[0], permutation_matrix.shape[1]))
                    for i in range(disparities):
                        permutation_3d[i, :, :] = permutation_matrix
                    temp_final = np.matmul(permutation_3d, temp_final)
                    #dashabi = np.swapaxes(temp_final, 0, 2)
                    #dashabi = np.swapaxes(dashabi, 0, 1)
                    #np.testing.assert_array_equal(dashabi,main_aggregation)

                    #np.matmul(permutation_matrix, temp_final[1])
                elif offset >= 0:
                    # 向右
                    permutation_matrix = np.zeros((width, width))
                    np.fill_diagonal(permutation_matrix[:, offset:], diagnal)
                    # stack permutation matrix on axis 0
                    permutation_3d = np.zeros((disparities, permutation_matrix.shape[0], permutation_matrix.shape[1]))
                    for i in range(disparities):
                        permutation_3d[i, :, :] = permutation_matrix

                    # final_result
                    # permutation_3d
                    temp_final = np.matmul(temp_final, permutation_3d)
                final_result = final_result + temp_final
                end2 = t.time()
                dur2 = end2 - start2
                if dur2 < dur1:
                    a += 1
            dashabi = np.swapaxes(final_result, 0, 2)
            dashabi = np.swapaxes(dashabi, 0, 1)
            np.testing.assert_array_equal(dashabi, main_aggregation)
        #    print(offset)


                # transpose a 3d matrix from (disparity, height, width) to (height, width, disparity)

        if main.direction == SW.direction:
            for offset in range(start, end):
                south_west = np.flipud(cost_volume).diagonal(offset=offset).T
                north_east = np.flip(south_west, axis=0)
                dim = south_west.shape[0]
                y_sw_idx, x_sw_idx = get_indices(offset, dim, SW.direction, height - 1)
                y_ne_idx = np.flip(y_sw_idx, axis=0)
                x_ne_idx = np.flip(x_sw_idx, axis=0)
                main_aggregation[y_sw_idx, x_sw_idx, :] = get_path_cost(south_west, 1, parameters)
                opposite_aggregation[y_ne_idx, x_ne_idx, :] = get_path_cost(north_east, 1, parameters)

        aggregation_volume[:, :, :, path_id] = main_aggregation
        aggregation_volume[:, :, :, path_id + 1] = opposite_aggregation
        path_id = path_id + 2

        dusk = t.time()
        print('\t(done in {:.2f}s)'.format(dusk - dawn))

    return aggregation_volume


def compute_costs(left, right, parameters, save_images):
    """
    first step of the sgm algorithm, matching cost based on census transform and hamming distance.
    :param left: left image.
    :param right: right image.
    :param parameters: structure containing parameters of the algorithm.
    :param save_images: whether to save census images or not.
    :return: H x W x D array with the matching costs.
    """
    assert left.shape[0] == right.shape[0] and left.shape[1] == right.shape[1], 'left & right must have the same shape.'
    assert parameters.max_disparity > 0, 'maximum disparity must be greater than 0.'

    height = left.shape[0]
    width = left.shape[1]
    cheight = parameters.csize[0]
    cwidth = parameters.csize[1]
    y_offset = int(cheight / 2)
    x_offset = int(cwidth / 2)
    disparity = parameters.max_disparity

    left_img_census = np.zeros(shape=(height, width), dtype=np.uint8)
    right_img_census = np.zeros(shape=(height, width), dtype=np.uint8)
    left_census_values = np.zeros(shape=(height, width), dtype=np.uint64)
    right_census_values = np.zeros(shape=(height, width), dtype=np.uint64)

    print('\tComputing left and right census...', end='')
    sys.stdout.flush()
    dawn = t.time()
    # pixels on the border will have no census values
    for y in range(y_offset, height - y_offset):
        for x in range(x_offset, width - x_offset):
            left_census = np.int64(0)
            center_pixel = left[y, x]
            reference = np.full(shape=(cheight, cwidth), fill_value=center_pixel, dtype=np.int64)
            image = left[(y - y_offset):(y + y_offset + 1), (x - x_offset):(x + x_offset + 1)]
            comparison = image - reference
            for j in range(comparison.shape[0]):
                for i in range(comparison.shape[1]):
                    if (i, j) != (y_offset, x_offset):
                        left_census = left_census << 1
                        if comparison[j, i] < 0:
                            bit = 1
                        else:
                            bit = 0
                        left_census = left_census | bit
            left_img_census[y, x] = np.uint8(left_census)
            left_census_values[y, x] = left_census

            right_census = np.int64(0)
            center_pixel = right[y, x]
            reference = np.full(shape=(cheight, cwidth), fill_value=center_pixel, dtype=np.int64)
            image = right[(y - y_offset):(y + y_offset + 1), (x - x_offset):(x + x_offset + 1)]
            comparison = image - reference
            for j in range(comparison.shape[0]):
                for i in range(comparison.shape[1]):
                    if (i, j) != (y_offset, x_offset):
                        right_census = right_census << 1
                        if comparison[j, i] < 0:
                            bit = 1
                        else:
                            bit = 0
                        right_census = right_census | bit
            right_img_census[y, x] = np.uint8(right_census)
            right_census_values[y, x] = right_census

    dusk = t.time()
    print('\t(done in {:.2f}s)'.format(dusk - dawn))

    if save_images:
        cv2.imwrite('left_census.png', left_img_census)
        cv2.imwrite('right_census.png', right_img_census)

    print('\tComputing cost volumes...', end='')
    sys.stdout.flush()
    dawn = t.time()
    left_cost_volume = np.zeros(shape=(height, width, disparity), dtype=np.uint32)
    right_cost_volume = np.zeros(shape=(height, width, disparity), dtype=np.uint32)
    lcensus = np.zeros(shape=(height, width), dtype=np.int64)
    rcensus = np.zeros(shape=(height, width), dtype=np.int64)
    for d in range(0, disparity):
        rcensus[:, (x_offset + d):(width - x_offset)] = right_census_values[:, x_offset:(width - d - x_offset)]
        left_xor = np.int64(np.bitwise_xor(np.int64(left_census_values), rcensus))
        left_distance = np.zeros(shape=(height, width), dtype=np.uint32)
        while not np.all(left_xor == 0):
            tmp = left_xor - 1
            mask = left_xor != 0
            left_xor[mask] = np.bitwise_and(left_xor[mask], tmp[mask])
            left_distance[mask] = left_distance[mask] + 1
        left_cost_volume[:, :, d] = left_distance

        lcensus[:, x_offset:(width - d - x_offset)] = left_census_values[:, (x_offset + d):(width - x_offset)]
        right_xor = np.int64(np.bitwise_xor(np.int64(right_census_values), lcensus))
        right_distance = np.zeros(shape=(height, width), dtype=np.uint32)
        while not np.all(right_xor == 0):
            tmp = right_xor - 1
            mask = right_xor != 0
            right_xor[mask] = np.bitwise_and(right_xor[mask], tmp[mask])
            right_distance[mask] = right_distance[mask] + 1
        right_cost_volume[:, :, d] = right_distance

    dusk = t.time()
    print('\t(done in {:.2f}s)'.format(dusk - dawn))

    return left_cost_volume, right_cost_volume


def select_disparity(aggregation_volume):
    """
    last step of the sgm algorithm, corresponding to equation 14 followed by winner-takes-all approach.
    :param aggregation_volume: H x W x D x N array of matching cost for all defined directions.
    :return: disparity image.
    """
    volume = np.sum(aggregation_volume, axis=3)
    disparity_map = np.argmin(volume, axis=2)
    return disparity_map


def normalize(volume, parameters):
    """
    transforms values from the range (0, 64) to (0, 255).
    :param volume: n dimension array to normalize.
    :param parameters: structure containing parameters of the algorithm.
    :return: normalized array.
    """
    return 255.0 * volume / parameters.max_disparity


def get_recall(disparity, gt, args):
    """
    computes the recall of the disparity map.
    :param disparity: disparity image.
    :param gt: path to ground-truth image.
    :param args: program arguments.
    :return: rate of correct predictions.
    """
    gt = np.float32(cv2.imread(gt, cv2.IMREAD_GRAYSCALE))
    gt = np.int16(gt / 255.0 * float(args.disp))
    disparity = np.int16(np.float32(disparity) / 255.0 * float(args.disp))
    correct = np.count_nonzero(np.abs(disparity - gt) <= 3)
    return float(correct) / gt.size


def sgm():
    """
    main function applying the semi-global matching algorithm.
    :return: void.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--left', default='cones/im2.png', help='name (path) to the left image')
    parser.add_argument('--right', default='cones/im6.png', help='name (path) to the right image')
    parser.add_argument('--left_gt', default='cones/disp2.png', help='name (path) to the left ground-truth image')
    parser.add_argument('--right_gt', default='cones/disp6.png', help='name (path) to the right ground-truth image')
    parser.add_argument('--output', default='disparity_map.png', help='name of the output image')
    parser.add_argument('--disp', default=64, type=int, help='maximum disparity for the stereo pair')
    parser.add_argument('--images', default=True, type=bool, help='save intermediate representations')
    parser.add_argument('--eval', default=True, type=bool, help='evaluate disparity map with 3 pixel error')
    args = parser.parse_args()

    left_name = args.left
    right_name = args.right
    left_gt_name = args.left_gt
    right_gt_name = args.right_gt
    output_name = args.output
    disparity = args.disp
    save_images = args.images
    evaluation = args.eval

    dawn = t.time()

    parameters = Parameters(max_disparity=disparity, P1=10, P2=120, csize=(7, 7), bsize=(3, 3))
    paths = Paths()

    print('\nLoading images...')
    left, right = load_images(left_name, right_name, parameters)

    print('\nStarting cost computation...')
    left_cost_volume, right_cost_volume = compute_costs(left, right, parameters, save_images)
    if save_images:
        left_disparity_map = np.uint8(normalize(np.argmin(left_cost_volume, axis=2), parameters))
        cv2.imwrite('disp_map_left_cost_volume.png', left_disparity_map)
        right_disparity_map = np.uint8(normalize(np.argmin(right_cost_volume, axis=2), parameters))
        cv2.imwrite('disp_map_right_cost_volume.png', right_disparity_map)

    print('\nStarting left aggregation computation...')
    left_aggregation_volume = aggregate_costs(left_cost_volume, parameters, paths)
    print('\nStarting right aggregation computation...')
    right_aggregation_volume = aggregate_costs(right_cost_volume, parameters, paths)

    print('\nSelecting best disparities...')
    left_disparity_map = np.uint8(normalize(select_disparity(left_aggregation_volume), parameters))
    right_disparity_map = np.uint8(normalize(select_disparity(right_aggregation_volume), parameters))
    if save_images:
        cv2.imwrite('left_disp_map_no_post_processing.png', left_disparity_map)
        cv2.imwrite('right_disp_map_no_post_processing.png', right_disparity_map)

    print('\nApplying median filter...')
    left_disparity_map = cv2.medianBlur(left_disparity_map, parameters.bsize[0])
    right_disparity_map = cv2.medianBlur(right_disparity_map, parameters.bsize[0])
    cv2.imwrite(f'left_{output_name}', left_disparity_map)
    cv2.imwrite(f'right_{output_name}', right_disparity_map)

    if evaluation:
        print('\nEvaluating left disparity map...')
        recall = get_recall(left_disparity_map, left_gt_name, args)
        print('\tRecall = {:.2f}%'.format(recall * 100.0))
        print('\nEvaluating right disparity map...')
        recall = get_recall(right_disparity_map, right_gt_name, args)
        print('\tRecall = {:.2f}%'.format(recall * 100.0))

    dusk = t.time()
    print('\nFin.')
    print('\nTotal execution time = {:.2f}s'.format(dusk - dawn))


if __name__ == '__main__':
    sgm()
